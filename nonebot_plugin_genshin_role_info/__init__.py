"""原神角色面板 —— NoneBot2 插件入口。

从真寻 Bot 插件 ``zhenxun_plugin_genshin_role_info`` v4.3.13 迁移而来。
本模块只负责「命令匹配 / 事件分发 / 消息发送」，业务逻辑位于
:mod:`~nonebot_plugin_genshin_role_info.data_source` 与
:mod:`~nonebot_plugin_genshin_role_info.utils`。

与原实现的对应关系：

* 真寻的 ``on_command(...).finish()/send()`` 语义被 :class:`Reply` 显式建模，
  由 :func:`_dispatch` 统一执行，避免把流程控制藏进依赖注入。
* 真寻的配置中心替换为 :mod:`config`（.env 注入）。
* 真寻的 ``AsyncHttpx`` 替换为 :mod:`utils.http_utils` 中的等价实现。
* 真寻 ``mys_sign`` 插件提供的米游社登录态替换为可选的用户配置提供方
  （见 :func:`_load_mys_provider`），未配置时该命令返回明确的配置指引，
  不再静默失败。
"""

from __future__ import annotations

import asyncio
import copy
import importlib
import os
import random
import re
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

import nonebot
from nonebot import get_driver, on_command, on_message, on_regex
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
)
from nonebot.adapters.onebot.v11.permission import GROUP, PRIVATE
from nonebot.params import CommandArg, RegexGroup
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata

from .config import PLUGIN_NAME, PLUGIN_VERSION, config
from .data_source.damage import get_role_dmg
from .data_source.damage.recommend import DamageTargetError
from .data_source.draw_artifact_card import draw_artifact_card
from .data_source.draw_recommend_card import (
    gen_artifact_adapt,
    gen_artifact_recommend,
    gen_suit_recommend,
)
from .data_source.draw_role_card import MissingIconSourceError, draw_role_card
from .data_source.draw_role_rank_card import draw_role_rank_card
from .data_source.draw_update_card import draw_role_pic
from .paths import DATA_PATH, ensure_data_dirs
from .utils.card_utils import (
    PlayerInfo,
    artifact_list as _artifact_list,
    get_name_by_id,
    group_info_path,
    json_path,
    load_json,
    other_path,
    player_info_path,
    role_info_json,
    save_json,
)
from .utils.download_utils import DownloadError
from .utils.http_utils import AsyncHttpx, close_client
from .utils.image_utils import image_build, load_image
from .utils.json_utils import get_message_at
from .utils.rank_utils import collect_damage_titles, collect_role_rank_entries

try:  # 更新检查是可选能力：缺少调度器时插件仍可正常使用
    from nonebot_plugin_apscheduler import scheduler
except Exception:  # pragma: no cover - 取决于部署环境
    scheduler = None


__plugin_meta__ = PluginMetadata(
    name=PLUGIN_NAME,
    description="原神角色面板：橱窗角色面板、圣遗物评分与推荐、群排行",
    usage=(
        "原神绑定UID / 原神解绑\n"
        "角色面板（例：甘雨面板、甘雨面板@用户、甘雨面板100000001）\n"
        "更新原神面板 / 刷新原神面板（末尾可带 UID）\n"
        "原神角色排行\n"
        "XX花/羽/沙/杯/冠适配、XX推荐、XX套推荐、XX独立套推荐（末尾可带伤害序号）\n"
        "XX评分排行、XX伤害排行N（仅群聊）\n"
        "最强XX / 最菜XX / 群最强XX / 群最菜XX\n"
        "圣遗物榜单、群圣遗物榜单、圣遗物导入\n"
        "重置最强XX（超级用户）、检查原神面板更新（超级用户）"
    ),
    type="application",
    homepage="https://github.com/NovaEvset/Genshin-RoleInfo-Plugin",
    supported_adapters={"~onebot.v11"},
    extra={
        "author": "CRAZYSHIMAKAZE",
        "version": PLUGIN_VERSION,
        "migrated_from": "zhenxun_plugin_genshin_role_info",
    },
)

__plugin_version__ = PLUGIN_VERSION

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
# 与原实现一致：首个接口失败后回退到第二个。
api_url = [
    url.strip()
    for url in config.genshin_role_info_api_urls.split(",")
    if url.strip()
]
headers = {"User-Agent": "Miao-Plugin/3.0"}

MYS_PROVIDER_ENV = "GENSHIN_ROLE_INFO_MYS_PROVIDER"

_role_info_json = role_info_json
_artifact_info = _artifact_list


# ---------------------------------------------------------------------------
# 回复语义
# ---------------------------------------------------------------------------
@dataclass
class Reply:
    """描述一次消息发送或终止。

    原实现直接调用 ``matcher.finish()`` / ``matcher.send()``；为了把业务逻辑
    与流程控制解耦，这里把该行为显式化，由 :func:`_dispatch` 执行。

    :param message: 待发送内容
    :param finish: ``True`` 等价于 ``matcher.finish()``（发送后终止该匹配器），
        ``False`` 等价于 ``matcher.send()``（发送后继续）
    :param at_sender: 是否 @ 发送者
    """

    message: Any = None
    finish: bool = False
    at_sender: bool = False


@dataclass
class Outcome:
    """业务函数的返回值容器。

    :param replies: 需要依次发送/终止的消息
    :param values: 供调用方继续处理的附加数据（例如查询到的 UID）
    """

    replies: list[Reply] = field(default_factory=list)
    values: dict[str, Any] = field(default_factory=dict)


def _reply_finish(message: Any = None, at_sender: bool = False) -> Reply:
    return Reply(message=message, finish=True, at_sender=at_sender)


def _matcher_handle(matcher: Any) -> Callable[[Callable[..., Awaitable[Outcome]]], Any]:
    """把返回 :class:`Outcome` 的业务函数注册为匹配器处理器。

    实现要点：包装函数通过 ``inspect.signature`` 继承原函数的签名，这样
    NoneBot2 的依赖注入（``Bot`` / ``MessageEvent`` / ``RegexGroup`` 等）仍能
    按原样解析；同时按 :class:`Reply` 的 ``finish`` 语义调用
    ``matcher.finish()``（发送后终止）或 ``matcher.send()``（发送后继续），
    与真寻版本的流程控制保持一致。
    """
    import functools

    def decorator(func: Callable[..., Awaitable[Outcome]]):
        @functools.wraps(func)
        async def _handler(*args: Any, **kwargs: Any) -> None:
            outcome = await func(*args, **kwargs)
            if outcome is None:
                return
            replies = outcome.replies
            for index, reply in enumerate(replies):
                is_last = index == len(replies) - 1
                if reply.finish and is_last:
                    # 等价于真寻的 matcher.finish()：发送后终止该匹配器。
                    await matcher.finish(reply.message, at_sender=reply.at_sender)
                else:
                    # 等价于 matcher.send()：发送后继续向下执行。
                    await matcher.send(reply.message, at_sender=reply.at_sender)

        return matcher.handle()(_handler)

    return decorator


# ---------------------------------------------------------------------------
# 匹配器
# ---------------------------------------------------------------------------
bind = on_regex(r"(?:原神绑定|绑定原神).*?(\d+)", priority=5, block=True)
unbind = on_command("原神解绑", priority=5, block=True)
card_list = on_command("原神角色排行", priority=4, block=True)

get_card = on_regex(r"(.*)面板(.*)", priority=4, block=False)
group_best = on_regex(r"^(最强|群最强)(.*)", priority=4)
group_worst = on_regex(r"^(最菜|群最菜)(.*)", priority=4)
role_rank = on_regex(
    r"^(.+?)(评分|伤害)(?:排行|榜单)(\d*)$", permission=GROUP, priority=4, block=True
)
artifact_adapt = on_regex("(.*?)([花羽沙杯冠])适配", priority=4)
artifact_recommend = on_regex(r"(.*?)([花羽沙杯冠套])推荐(\d*)$", priority=4)
artifact_list = on_command("圣遗物榜单", aliases={"圣遗物排行"}, priority=4, block=True)
group_artifact_list = on_command(
    "群圣遗物榜单", aliases={"群圣遗物排行"}, priority=4, block=True
)
reset_best = on_command("重置最强", permission=SUPERUSER, priority=3, block=False)
check_update = on_command("检查原神面板更新", permission=SUPERUSER, priority=3, block=True)
import_artifact = on_message(permission=PRIVATE, priority=1, block=False)
import_artifact_hint = on_command("圣遗物导入", priority=4, block=True)
check = on_command("gsck", permission=SUPERUSER, priority=4, block=True)
mys_update_panel = on_command("米游社更新原神面板", priority=3, block=True)


# ---------------------------------------------------------------------------
# UID 绑定存储
# ---------------------------------------------------------------------------
def get_uid(user_qq: int | str) -> str:
    qq2uid = load_json(f"{player_info_path}/qq2uid.json")
    return qq2uid.get(str(user_qq), "")


def bind_uid(user_qq: int | str, uid: int | str) -> None:
    qq2uid = load_json(f"{player_info_path}/qq2uid.json")
    qq2uid[str(user_qq)] = uid
    save_json(qq2uid, f"{player_info_path}/qq2uid.json")


def unbind_uid(user_qq: int | str) -> None:
    qq2uid = load_json(f"{player_info_path}/qq2uid.json")
    qq2uid.pop(str(user_qq), None)
    save_json(qq2uid, f"{player_info_path}/qq2uid.json")


def check_uid(uid: Any) -> bool:
    """手动绑定的 UID 必须为 9 位且首位为 1-9。"""
    return re.search(r"^[123456789]\d{8}$", str(uid)) is not None


def get_role_name(role: str) -> str:
    """把别名解析为规范角色名，未识别时返回空串。"""
    role = (role or "").strip()
    for item in _role_info_json.keys():
        if role == item or role in (_role_info_json.get(item, {}).get("别名") or []):
            return item
    return ""


async def get_msg_uid(event: MessageEvent, matcher: Any) -> str | None:
    """取消息中 @ 的用户或发送者所绑定的 UID。

    未绑定时返回 ``None``，并已在 :class:`Outcome` 中给出提示。
    """
    at_user = get_message_at(event.json())
    user_qq = at_user[0] if at_user else event.user_id
    uid = get_uid(user_qq)
    return uid or None


def _unbound_outcome() -> Outcome:
    return Outcome([_reply_finish("请发送'绑定原神uidxxxx'后再查询！")])


# ---------------------------------------------------------------------------
# 橱窗数据获取（MicroGG / Enka）
# ---------------------------------------------------------------------------
_STATUS_HINT = {
    400: "UID 格式错误...",
    404: "玩家不存在（MHY 服务器说的）...",
    424: "查询接口维护中...",
    429: "请求频率限制（被我的或者MHY的服务器）...",
    500: "服务器错误...",
    503: "我搞砸了...",
}


async def get_enka_info(
    uid: str, update_info: bool, event: MessageEvent
) -> tuple[PlayerInfo | None, list[str], Outcome | None]:
    """读取（必要时下载）玩家橱窗数据。

    :return: ``(player_info, 更新的角色列表, 终止回复)``；第三项非空表示需要
        终止当前命令并按该回复返回。
    """
    update_role_list: list[str] = []
    cache_file = f"{player_info_path}/{uid}.json"

    if not os.path.exists(cache_file) or update_info:
        req = None
        last_status: int | None = None
        for url_template in api_url[:2]:
            try:
                url = url_template.format(uid)
            except (IndexError, KeyError):
                # 允许用户把占位符写成 {uid}；两种写法都支持
                url = url_template.replace("{uid}", str(uid))
            try:
                content = await AsyncHttpx.get_content(
                    url,
                    headers=headers,
                    timeout=config.genshin_role_info_request_timeout,
                )
                req = content
                last_status = 200
                break
            except Exception as error:
                last_status = getattr(
                    getattr(error, "response", None), "status_code", None
                )
                nonebot.logger.warning(
                    f"{PLUGIN_NAME}: 请求 {url} 失败: {error}"
                )
                continue
        else:
            hint = _STATUS_HINT.get(last_status, "未知问题...")
            return None, [], Outcome([_reply_finish(hint)])

        import json as _json

        data = _json.loads(req.decode("utf-8"))
        player_info = PlayerInfo(uid)
        player_info.set_player(data["playerInfo"])
        if data.get("avatarInfoList", ""):
            for role in data["avatarInfoList"]:
                try:
                    player_info.set_role(role)
                    update_role_list.append(get_name_by_id(str(role["avatarId"])))
                except Exception as error:
                    nonebot.logger.warning(f"{PLUGIN_NAME}: 解析角色失败: {error}")
            player_info.save()
        else:
            guide = load_image(f"{other_path}/collections.png")
            guide = image_build(img=guide, quality=100, mode="RGB")
            return (
                None,
                [],
                Outcome([_reply_finish(guide + "在游戏中打开显示详情选项!")]),
            )
    else:
        player_info = PlayerInfo(uid)
    return player_info, update_role_list, None


async def check_artifact(
    event: MessageEvent,
    player_info: PlayerInfo,
    roles_list: list[str],
    uid: str,
    group_save: bool,
) -> None:
    """重建圣遗物列表、角色评分与榜单缓存。"""
    artifact_all = player_info.data["圣遗物列表"]
    pos_name = ["生之花", "死之羽", "时之沙", "空之杯", "理之冠"]
    for i in range(5):
        for item in player_info.data["圣遗物列表"][i]:
            if "角色" not in item:
                item["角色"] = ""
            elif item["角色"] in roles_list:
                item["角色"] = ""
        for role_name in roles_list:
            role_data = player_info.get_roles_info(role_name)
            if role_data is None:
                continue
            artifact_list = [{} for _ in range(5)]
            if "圣遗物" not in role_data:
                continue
            for j in range(len(role_data["圣遗物"])):
                artifact_list[pos_name.index(role_data["圣遗物"][j]["部位"])] = (
                    role_data["圣遗物"][j]
                )
            artifact_copy = copy.deepcopy(artifact_list[i])
            if artifact_copy.get("等级", 0) >= 10:
                artifact_copy["角色"] = role_name
                artifact_all[i].append(artifact_copy)

    roles_list = player_info.get_roles_list()
    player_info.data["圣遗物榜单"] = []
    player_info.data["大毕业圣遗物"] = 0
    player_info.data["小毕业圣遗物"] = 0

    for role_name in roles_list:
        role_data = player_info.get_roles_info(role_name)
        try:
            await draw_role_card(
                uid, role_data, player_info, __plugin_version__, only_cal=True
            )
        except Exception as error:
            nonebot.logger.debug(
                f"{PLUGIN_NAME}: 计算 {role_name} 评分失败: {error}"
            )
    player_info.save()
    if group_save and isinstance(event, GroupMessageEvent):
        check_group_artifact(event, player_info)


async def check_role_available(
    role_name: str, roles_list: list[str], event: MessageEvent
) -> Outcome | None:
    """校验角色是否在展柜中，返回非空表示需要终止。"""
    guide = None
    if not roles_list:
        guide = load_image(f"{other_path}/collections.png")
        guide = image_build(img=guide, quality=100, mode="RGB")
        return Outcome(
            [
                _reply_finish(
                    guide + "无角色信息,在游戏中将角色放入展柜并输入更新原神面板!"
                )
            ]
        )
    if role_name not in roles_list:
        return Outcome(
            [
                _reply_finish(
                    f"角色展柜里没有{role_name}的信息哦!可查询:{','.join(roles_list)}"
                )
            ]
        )
    return None


# ---------------------------------------------------------------------------
# 命令：绑定 / 解绑
# ---------------------------------------------------------------------------
@_matcher_handle(bind)
async def _(
    event: MessageEvent, arg: tuple[str, ...] = RegexGroup()
) -> Outcome:
    msg = arg[0].strip()
    uid = get_uid(event.user_id)
    if not msg.isdigit():
        return Outcome([_reply_finish("uid/id必须为纯数字！")])
    msg_int = int(msg)
    if uid:
        return Outcome(
            [
                _reply_finish(
                    f"您已绑定过uid：{uid}，如果希望更换uid，请先发送原神解绑"
                )
            ]
        )
    if not check_uid(msg_int):
        return Outcome([_reply_finish(f"绑定的uid{msg_int}不合法，请重新绑定!")])
    bind_uid(event.user_id, msg_int)
    return Outcome([_reply_finish(f"已成功添加原神uid：{msg_int}")])


@_matcher_handle(unbind)
async def _(event: MessageEvent) -> Outcome:
    if get_uid(event.user_id):
        unbind_uid(event.user_id)
        return Outcome([Reply("用户数据删除成功...")])
    return Outcome()


# ---------------------------------------------------------------------------
# 命令：圣遗物导入
# ---------------------------------------------------------------------------
@_matcher_handle(import_artifact_hint)
async def _() -> Outcome:
    return Outcome(
        [
            Reply(
                "请在PC端按以下步骤操作\n"
                "1.下载https://github.com/1803233552/yas/releases/download/"
                "v0.1.28/yas_artifact_v0.1.28.exe\n"
                "2.打开原神，切换到背包圣遗物页面，将背包拉到最上面\n"
                "3.在该目录下命令行窗口输入"
                "./yas_artifact_v0.1.28.exe -f good --min-level 20命令,开始扫描\n"
                "4.扫描完成后，添加机器人为好友，将生成的good.json文件"
                "私聊发送给机器人即可。"
            )
        ]
    )


@_matcher_handle(import_artifact)
async def _(bot: Bot, event: MessageEvent) -> Outcome:
    file_segment = next(
        (segment for segment in event.message if segment.type == "file"), None
    )
    if not file_segment or file_segment.data.get("file") != "good.json":
        return Outcome()
    file_id = file_segment.data.get("file_id")
    if not file_id:
        await bot.send_private_msg(
            user_id=event.user_id, message="good.json缺少文件标识，请重新发送。"
        )
        return Outcome()

    convert = load_json(path=f"{json_path}/convert.json")
    role = _role_info_json
    name = convert.get("名称")
    icon = _artifact_info.get("Name")
    suit = convert.get("套装")
    pos = convert.get("位置")
    main = convert.get("主词条")
    sub = convert.get("副词条")

    uid = await get_msg_uid(event, import_artifact)
    if uid is None:
        return _unbound_outcome()

    player_info, _, terminated = await get_enka_info(uid, update_info=False, event=event)
    if terminated is not None:
        return terminated

    try:
        file_info = await bot.call_api("get_private_file_url", file_id=file_id)
        file_url = file_info.get("url", "")
        if not file_url:
            raise ValueError("未获取到私聊文件下载地址")
        decoded_data = await AsyncHttpx.get_content(file_url)
    except Exception as error:
        nonebot.logger.warning(f"{PLUGIN_NAME}: good.json读取失败: {error}")
        await bot.send_private_msg(
            user_id=event.user_id,
            message="good.json读取失败，请重新发送文件后再试。",
        )
        return Outcome()

    with open(f"{player_info_path}/{uid}_artifact.json", "wb") as file:
        file.write(decoded_data)
    try:
        arti = load_json(path=f"{player_info_path}/{uid}_artifact.json")
    except Exception as error:
        nonebot.logger.warning(f"{PLUGIN_NAME}: good.json解析失败: {error}")
        await bot.send_private_msg(
            user_id=event.user_id, message="good.json格式不正确，无法导入。"
        )
        return Outcome()

    if "artifacts" in arti:
        await bot.send_private_msg(
            user_id=event.user_id, message="检测到圣遗物缓存文件,正在导入..."
        )
        same = 0
        diff = 0
        player_info.data["圣遗物列表"] = [[], [], [], [], []]
        conver_name = ["生之花", "死之羽", "时之沙", "空之杯", "理之冠"]
        for item in arti.get("artifacts"):
            if item.get("level", 0) == 20 and item.get("rarity", 0) == 5:
                mainstatkey = item.get("mainStatKey", "")
                substats = item.get("substats", [])
                pos_id = pos.get(item.get("slotKey", ""))
                pos_name = conver_name[pos_id]
                arti_name = name.get(suit.get(item.get("setKey", "")))[pos_id]
                icon_name = None
                for key, value in icon.items():
                    if value == arti_name:
                        icon_name = key
                        break
                role_name = ""
                for key, value in role.items():
                    if value.get("英文名") == item.get("location", ""):
                        role_name = key
                        break
                artifact_single = {
                    "名称": arti_name,
                    "图标": icon_name,
                    "部位": pos_name,
                    "所属套装": suit.get(item.get("setKey", "")),
                    "等级": item.get("level", 0),
                    "星级": item.get("rarity", 0),
                    "角色": role_name,
                    "主属性": {
                        "属性名": main.get(mainstatkey, "")["属性名"],
                        "属性值": main.get(mainstatkey, "")["属性值"],
                    },
                    "词条": [
                        {
                            "属性名": sub.get(substats[index].get("key", "")),
                            "属性值": int(substats[index].get("value", 0))
                            if substats[index].get("value", 0) % 1 == 0
                            else round(substats[index].get("value", 0), 1),
                        }
                        for index in range(4)
                    ],
                }
                if artifact_single not in player_info.data["圣遗物列表"][pos_id]:
                    player_info.data["圣遗物列表"][pos_id].append(
                        copy.deepcopy(artifact_single)
                    )
                    diff += 1
                else:
                    same += 1
        player_info.save()
        await bot.send_private_msg(
            user_id=event.user_id,
            message=f"UID{uid}的缓存圣遗物已导入,本次共导入{diff}个,重复圣遗物{same}个.",
        )
    return Outcome()


# ---------------------------------------------------------------------------
# 命令：圣遗物适配 / 推荐
# ---------------------------------------------------------------------------
@_matcher_handle(artifact_adapt)
async def _(
    bot: Bot, event: MessageEvent, args: tuple[str, ...] = RegexGroup()
) -> Outcome:
    msg = args[0].strip(), args[1].strip()
    uid = await get_msg_uid(event, artifact_adapt)
    if uid is None:
        return _unbound_outcome()
    if msg[1] not in ["花", "羽", "沙", "杯", "冠"]:
        return Outcome(
            [_reply_finish("请输入正确角色名和圣遗物名称(花羽沙杯冠)...", at_sender=True)]
        )
    role = msg[0]
    pos = ["花", "羽", "沙", "杯", "冠"].index(msg[1])
    role_name = get_role_name(role)
    if not role_name:
        return Outcome()
    player_info, _, terminated = await get_enka_info(uid, update_info=False, event=event)
    if terminated is not None:
        return terminated
    roles_list = player_info.get_roles_list()
    blocked = await check_role_available(role_name, roles_list, event)
    if blocked is not None:
        return blocked
    role_data = player_info.get_roles_info(role_name)
    pos_list = 0
    for index, item in enumerate(role_data["圣遗物"]):
        if item["部位"] == ["生之花", "死之羽", "时之沙", "空之杯", "理之冠"][pos]:
            pos_list = index
            break
    else:
        return Outcome([_reply_finish(f"{role_name}{pos}号位没有圣遗物！")])
    img, _ = await gen_artifact_adapt(
        f"{role}圣遗物适配({msg[1]})",
        role_data["圣遗物"][pos_list],
        uid,
        role,
        pos,
        __plugin_version__,
    )
    return Outcome([Reply(img)])


@_matcher_handle(artifact_recommend)
async def _(
    bot: Bot, event: MessageEvent, args: tuple[str, ...] = RegexGroup()
) -> Outcome:
    msg = args[0].strip(), args[1].strip()
    damage_index = int(args[2]) if args[2] else None
    uid = await get_msg_uid(event, artifact_recommend)
    if uid is None:
        return _unbound_outcome()
    main_prop = [
        "爆伤", "暴击", "精通", "生命", "防御", "攻击",
        "火伤", "冰伤", "雷伤", "物伤", "风伤", "水伤", "岩伤", "草伤",
        "治疗", "充能",
    ]
    suit_list = _artifact_info.get("Info")
    element = ""
    suit_name = ""
    is_suit = False
    occupy = False
    info = msg[0]
    for item in main_prop:
        if item in info:
            element = item
            info = info.replace(item, "")
            break
    if "独立" in info:
        info = info.replace("独立", "")
        occupy = True
    for item in suit_list:
        if item[:2] in info:
            suit_name = item
            info = info.replace(item[:2], "")
            break
    role = info
    pos = ["花", "羽", "沙", "杯", "冠", "套"].index(msg[1])
    if pos == 5:
        is_suit = True
    role_name = get_role_name(role)
    if not role_name:
        return Outcome()
    player_info, _, terminated = await get_enka_info(uid, update_info=False, event=event)
    if terminated is not None:
        return terminated
    artifact_pos_list: list = []
    if not is_suit:
        artifact_pos_list = player_info.get_artifact_list(pos)
        if not artifact_pos_list:
            return Outcome(
                [_reply_finish(f"{pos}号位没有圣遗物缓存！请先执行'更新面板'指令！")]
            )
    roles_list = player_info.get_roles_list()
    blocked = await check_role_available(role_name, roles_list, event)
    if blocked is not None:
        return blocked
    role_data = player_info.get_roles_info(role_name)
    command_suffix = str(damage_index) if damage_index is not None else ""
    recommendation_target = (
        f"{suit_name or ''}{element or ''}{'套装' if is_suit else msg[1]}"
    )
    recommendation_metric = "伤害" if damage_index is not None else "评分"
    await artifact_recommend.send(
        f"正在从圣遗物缓存中匹配{recommendation_metric}最高的"
        f"{recommendation_target}搭配，请稍后...",
        at_sender=True,
    )
    try:
        if not is_suit:
            img, target_title = await gen_artifact_recommend(
                f"{role_name}{suit_name}{element}{msg[1]}推荐{command_suffix}",
                role_data,
                artifact_pos_list,
                uid,
                role_name,
                pos,
                element,
                suit_name,
                __plugin_version__,
                damage_index,
                player_info,
            )
        else:
            img, target_title = await gen_suit_recommend(
                f"{suit_name}{element}套推荐{command_suffix}",
                role_data,
                player_info,
                uid,
                role_name,
                suit_name,
                occupy,
                __plugin_version__,
                damage_index,
            )
    except DamageTargetError as error:
        return Outcome([_reply_finish(str(error), at_sender=True)])
    except (DownloadError, MissingIconSourceError):
        return Outcome(
            [_reply_finish("圣遗物图标资源获取失败，请更新原神面板后重试。", at_sender=True)]
        )
    if not img:
        return Outcome([_reply_finish("未找到符合条件的圣遗物推荐!")])
    target_note = (
        f"目标伤害：{damage_index}.{target_title}\n"
        if damage_index is not None and target_title
        else ""
    )
    return Outcome(
        [
            Reply(
                img
                + target_note
                + "注:仅根据当前缓存圣遗物进行推荐,发送'圣遗物导入'可导入背包内所有圣遗物."
            )
        ]
    )


# ---------------------------------------------------------------------------
# 命令：榜单
# ---------------------------------------------------------------------------
@_matcher_handle(group_artifact_list)
async def _(bot: Bot, event: MessageEvent) -> Outcome:
    group_id = getattr(event, "group_id", None)
    if not os.path.exists(f"{group_info_path}/{group_id}.json"):
        return Outcome([_reply_finish("未收录任何圣遗物信息,请先进行查询!")])
    group_artifact_info = load_json(f"{group_info_path}/{group_id}.json")
    img, _ = await draw_artifact_card(
        "群圣遗物榜单",
        None,
        group_id,
        group_artifact_info,
        None,
        None,
        __plugin_version__,
        1,
    )
    return Outcome([Reply(img)])


@_matcher_handle(artifact_list)
async def _(bot: Bot, event: MessageEvent) -> Outcome:
    uid = await get_msg_uid(event, artifact_list)
    if uid is None:
        return _unbound_outcome()
    if not os.path.exists(f"{player_info_path}/{uid}.json"):
        return Outcome([_reply_finish("未收录任何角色信息,请先进行角色查询!")])
    player_info = PlayerInfo(uid)
    if not player_info.data["圣遗物榜单"]:
        return Outcome([_reply_finish("未收录任何圣遗物信息,请先输入'更新面板'命令!")])
    img, text = await draw_artifact_card(
        "圣遗物榜单",
        None,
        uid,
        player_info.data["圣遗物榜单"],
        player_info.data["大毕业圣遗物"],
        player_info.data["小毕业圣遗物"],
        __plugin_version__,
    )
    return Outcome([Reply(img + text)])


@_matcher_handle(role_rank)
async def _(
    bot: Bot, event: GroupMessageEvent, args: tuple[str, ...] = RegexGroup()
) -> Outcome:
    role_name = get_role_name(args[0].strip())
    metric = args[1]
    damage_index_text = args[2]
    if not role_name:
        return Outcome([_reply_finish()])
    if metric == "伤害" and (not damage_index_text or int(damage_index_text) < 1):
        try:
            members = await bot.get_group_member_list(group_id=event.group_id)
            uid_map = load_json(f"{player_info_path}/qq2uid.json")
        except Exception:
            return Outcome([_reply_finish("暂时无法获取伤害项目列表，请稍后重试。")])
        damage_titles = await asyncio.to_thread(
            collect_damage_titles,
            members,
            uid_map,
            player_info_path,
            role_name,
            get_role_dmg,
        )
        if damage_titles:
            damage_items = "\n".join(
                f"{index}. {title}"
                for index, title in enumerate(damage_titles, start=1)
            )
            return Outcome(
                [
                    _reply_finish(
                        f"请指定{role_name}伤害面板中的项目序号，例如："
                        f"{role_name}伤害排行1\n"
                        f"各数字对应的伤害项目：\n{damage_items}"
                    )
                ]
            )
        return Outcome(
            [
                _reply_finish(
                    f"请指定{role_name}伤害面板中的项目序号，例如："
                    f"{role_name}伤害排行1\n"
                    "暂未找到该角色的伤害面板，请先让群成员更新面板后重试。"
                )
            ]
        )
    if metric == "评分" and damage_index_text:
        return Outcome(
            [_reply_finish(f"评分排行不需要项目序号，请发送：{role_name}评分排行")]
        )
    damage_index = int(damage_index_text) if damage_index_text else None
    try:
        members = await bot.get_group_member_list(group_id=event.group_id)
    except Exception:
        return Outcome([_reply_finish("获取群成员列表失败，请稍后重试。")])

    uid_map = load_json(f"{player_info_path}/qq2uid.json")
    # 伤害排行需要对每个群成员执行一次同步伤害计算（实测约 10ms/次），
    # 放到线程池以避免阻塞事件循环。
    entries = await asyncio.to_thread(
        collect_role_rank_entries,
        members,
        uid_map,
        player_info_path,
        role_name,
        metric,
        get_role_dmg,
        damage_index,
    )
    if not entries:
        if metric == "伤害":
            return Outcome(
                [
                    _reply_finish(
                        f"本群暂无可用于{role_name}第{damage_index}个伤害项目的排行数据，"
                        "请确认序号未超出伤害面板范围，并让群成员更新原神面板。"
                    )
                ]
            )
        return Outcome(
            [
                _reply_finish(
                    f"本群暂无可用于{role_name}{metric}排行的数据，"
                    "请群成员先绑定 UID 并更新原神面板。"
                )
            ]
        )
    image = await draw_role_rank_card(
        f"{role_name}{metric}排行{damage_index or ''}",
        role_name,
        event.group_id,
        entries,
        __plugin_version__,
        metric,
    )
    return Outcome([Reply(image_build(image, quality=100))])


# ---------------------------------------------------------------------------
# 命令：最强 / 最菜
# ---------------------------------------------------------------------------
async def _resolve_member_name(bot: Bot, event: GroupMessageEvent, user_id: int) -> str:
    try:
        info = await bot.get_group_member_info(
            group_id=event.group_id, user_id=user_id
        )
        return info["card"] or info["nickname"]
    except Exception:
        stranger = await bot.get_stranger_info(user_id=user_id)
        return stranger["nickname"]


async def _group_role_extreme(
    bot: Bot, event: GroupMessageEvent, args: tuple[str, ...], best: bool
) -> Outcome:
    """``最强XX`` / ``最菜XX``：按圣遗物评分取群内持久化的角色面板图。"""
    role = get_role_name(args[1].strip())
    if not role:
        return Outcome()
    role_path = f"{group_info_path}/{event.group_id}/{role}"
    exists = os.path.exists(role_path)
    if best:
        if not exists:
            return Outcome([_reply_finish(f"本群还没有{role}的数据收录哦！赶快去查询吧！")])
    else:
        if not exists or len(os.listdir(role_path)) < 2:
            return Outcome([_reply_finish(f"本群还没有最菜{role}的数据收录哦！赶快去查询吧！")])

    data = sorted(os.listdir(role_path), key=lambda x: float(x.split("-")[0]))
    role_info = data[-1] if best else data[0]
    role_pic = load_image(f"{role_path}/{role_info}")
    role_pic = image_build(img=role_pic, quality=100, mode="RGB")
    user_id = int(role_info.split("-")[-1].rstrip(".png"))
    qq_name = await _resolve_member_name(bot, event, user_id)
    prefix = "最强" if best else "最菜"
    return Outcome([Reply(f"本群{prefix}{role}!仅根据圣遗物评分评判.\n由'{qq_name}'查询\n" + role_pic)])


@_matcher_handle(group_best)
async def _(event: GroupMessageEvent, args: tuple[str, ...] = RegexGroup()) -> Outcome:
    return await _group_role_extreme(nonebot.get_bot(), event, args, best=True)


@_matcher_handle(group_worst)
async def _(event: GroupMessageEvent, args: tuple[str, ...] = RegexGroup()) -> Outcome:
    return await _group_role_extreme(nonebot.get_bot(), event, args, best=False)


@_matcher_handle(reset_best)
async def _(event: GroupMessageEvent, arg: Message = CommandArg()) -> Outcome:
    role = get_role_name(arg.extract_plain_text().strip())
    if not role:
        return Outcome()
    role_path = f"{group_info_path}/{event.group_id}/{role}"
    shutil.rmtree(role_path, ignore_errors=True)
    return Outcome([_reply_finish(f"重置群{role}成功!")])


# ---------------------------------------------------------------------------
# 命令：角色面板 / 更新
# ---------------------------------------------------------------------------
@_matcher_handle(card_list)
async def _(event: MessageEvent, arg: Message = CommandArg()) -> Outcome:
    msg = arg.extract_plain_text().strip().split()
    if msg:
        return Outcome()
    uid = await get_msg_uid(event, card_list)
    if uid is None:
        return _unbound_outcome()
    return await get_char(uid, event)


async def get_char(uid: str, event: MessageEvent) -> Outcome:
    player_info, _, terminated = await get_enka_info(uid, update_info=False, event=event)
    if terminated is not None:
        return terminated
    roles_list = player_info.get_roles_list()
    if not roles_list:
        guide = load_image(f"{other_path}/collections.png")
        guide = image_build(img=guide, quality=100, mode="RGB")
        return Outcome(
            [
                _reply_finish(
                    guide
                    + "无角色信息,在游戏中将角色放入展柜并输入更新角色卡XXXX(uid)!"
                )
            ]
        )
    return Outcome([Reply(await draw_role_pic(uid, roles_list, player_info))])


async def gen(
    event: MessageEvent, uid: str, role_name: str, at_user: str
) -> Outcome:
    """绘制单个角色的面板卡片。"""
    player_info, _, terminated = await get_enka_info(uid, update_info=False, event=event)
    if terminated is not None:
        return terminated
    roles_list = player_info.get_roles_list()
    blocked = await check_role_available(role_name, roles_list, event)
    if blocked is not None:
        return blocked
    role_data = player_info.get_roles_info(role_name)
    try:
        img, score = await draw_role_card(
            uid, role_data, player_info, __plugin_version__, only_cal=False
        )
    except (DownloadError, MissingIconSourceError):
        return Outcome(
            [_reply_finish("面板图标资源获取失败，请更新原神面板后重试。")]
        )
    msg = "" if at_user else check_role(role_name, event, img, score)
    img = image_build(img=img, quality=100, mode="RGB")
    return Outcome([Reply(msg + img)])


async def update(event: MessageEvent, uid: str, group_save: bool) -> Outcome:
    """强制刷新橱窗数据并重建缓存。"""
    if os.path.exists(f"{player_info_path}/{uid}.json"):
        data = load_json(f"{player_info_path}/{uid}.json")
        if "玩家信息" in data.keys():
            given_time_str = data["玩家信息"]["更新时间"]
            given_time = datetime.strptime(given_time_str, "%Y-%m-%d %H:%M:%S")
            current_time = datetime.now()
            time_difference_seconds = (current_time - given_time).total_seconds()
            mod_time = os.path.getmtime(f"{player_info_path}/{uid}.json")
            cd_time = int(time.time() - mod_time)
            if time_difference_seconds < 60:
                return Outcome([_reply_finish(f"{60 - cd_time}秒后可再次更新!")])
    player_info, update_role_list, terminated = await get_enka_info(
        uid, update_info=True, event=event
    )
    if terminated is not None:
        return terminated
    await check_artifact(event, player_info, update_role_list, uid, group_save)
    return Outcome([Reply(await draw_role_pic(uid, update_role_list, player_info))])


@_matcher_handle(get_card)
async def _(event: MessageEvent, args: tuple[str, ...] = RegexGroup()) -> Outcome:
    role = args[0].strip()
    at_user = args[1].strip()
    merge_ = role + at_user
    if "原神" not in merge_:
        role = get_role_name(role)
    if not role:
        return Outcome()
    if at_user.isdigit():
        uid: str = at_user
    else:
        resolved = await get_msg_uid(event, get_card)
        if resolved is None:
            return _unbound_outcome()
        uid = resolved
    if ("更新" in merge_ or "刷新" in merge_) and "原神" in merge_:
        if at_user:
            return await update(event, uid, group_save=False)
        return await update(event, uid, group_save=True)
    return await gen(event, uid, role, at_user=at_user)


def check_role(
    role_name: str, event: MessageEvent, img: Any, score: Any
) -> Any:
    """记录群内最强/最菜角色面板图，并返回提示文案。"""
    if isinstance(event, GroupMessageEvent) and str(score)[-1] != "*":
        role_path = f"{group_info_path}/{event.group_id}/{role_name}/{score}-{event.user_id}.png"
        role_path = Path(role_path)
        role_path.parent.mkdir(parents=True, exist_ok=True)
        if not os.listdir(role_path.parent):
            img.save(role_path)
            return f"恭喜成为本群最强{role_name}!\n"
        data = sorted(os.listdir(role_path.parent), key=lambda x: float(x.split("-")[0]))
        role_info_best = data[-1].split("-")
        if len(os.listdir(role_path.parent)) == 1:
            img.save(role_path)
            if float(role_info_best[0]) <= score:
                old_best = int(role_info_best[1].rstrip(".png"))
                if old_best != event.user_id:
                    return Message(
                        f"恭喜你击败{MessageSegment.at(old_best)}成为本群最强{role_name}!\n"
                    )
                return f"你仍然是本群最强{role_name}!\n"
            return (
                f"恭喜你成为本群最菜{role_name}!\n"
                f"距本群最强{role_name}还有"
                f"{round(float(role_info_best[0]) - score, 2)}分差距!\n"
            )
        if float(role_info_best[0]) <= score:
            os.unlink(f"{role_path.parent}/{data[-1]}")
            img.save(role_path)
            old_best = int(role_info_best[1].rstrip(".png"))
            if old_best != event.user_id:
                return Message(
                    f"恭喜你击败{MessageSegment.at(old_best)}成为本群最强{role_name}!\n"
                )
            return f"你仍然是本群最强{role_name}!\n"
        role_info_worst = data[0].split("-")
        if float(role_info_worst[0]) >= score:
            os.unlink(f"{role_path.parent}/{data[0]}")
            img.save(role_path)
            old_worst = int(role_info_worst[1].rstrip(".png"))
            if old_worst != event.user_id:
                return Message(
                    f"恭喜你帮助{MessageSegment.at(old_worst)}摆脱最菜{role_name}的头衔!\n"
                )
            return (
                f"你仍然是本群最菜{role_name}!\n"
                f"距本群最强{role_name}还有"
                f"{round(float(role_info_best[0]) - score, 2)}分差距!\n"
            )
        return (
            f"距本群最强{role_name}还有"
            f"{round(float(role_info_best[0]) - score, 2)}分差距!\n"
        )
    return ""


def check_group_artifact(event: GroupMessageEvent, player_info: PlayerInfo) -> None:
    """把个人圣遗物榜单合并进群榜单，保留评分最高的 20 件。"""
    if not os.path.exists(f"{group_info_path}/{event.group_id}.json"):
        group_artifact_info: list = []
    else:
        group_artifact_info = load_json(f"{group_info_path}/{event.group_id}.json")
    group_player_info = copy.deepcopy(player_info.data["圣遗物榜单"])
    for item in group_player_info:
        item["QQ"] = event.user_id
        if item not in group_artifact_info:
            group_artifact_info.append(item)
    group_artifact_info_20 = sorted(
        group_artifact_info, key=lambda x: float(x["评分"]), reverse=True
    )[:20]
    save_json(group_artifact_info_20, f"{group_info_path}/{event.group_id}.json")


# ---------------------------------------------------------------------------
# 可选：米游社面板同步
# ---------------------------------------------------------------------------
def _load_mys_provider() -> Any | None:
    """加载可选的米游社登录态提供方。

    真寻版本直接依赖同仓库的 ``mys_sign`` 插件（扫码登录 + MysAPI）。该插件
    不属于标准 NoneBot2 生态，因此这里改为约定式注入：环境变量
    ``GENSHIN_ROLE_INFO_MYS_PROVIDER`` 指向 ``module:attr``，该对象需提供

    * ``load_user(qq_id) -> dict | None``
    * ``MysAPI()`` 实例，需实现 ``async get_genshin_character_details(user, uid=..., region=..., captcha_notifier=...)``
      与 ``async close()``

    未配置时本命令给出明确的配置指引，而不是静默失败。
    """
    spec = os.environ.get(MYS_PROVIDER_ENV, "").strip()
    if not spec:
        return None
    try:
        module_name, _, attr = spec.partition(":")
        module = importlib.import_module(module_name)
        return getattr(module, attr) if attr else module
    except Exception as error:
        nonebot.logger.error(
            f"{PLUGIN_NAME}: 加载米游社提供方 {spec!r} 失败: {error}"
        )
        return None


def _mys_role_error(user: dict | None) -> str:
    if not user:
        return "请先发送「扫码登录」绑定米游社账号"
    roles = user.get("game_roles", {}).get("genshin", [])
    if not isinstance(roles, list) or not roles:
        return "当前米游社账号没有原神角色"
    chosen_count = sum(
        isinstance(role, dict) and role.get("is_chosen") is True for role in roles
    )
    if len(roles) > 1 and chosen_count != 1:
        return "检测到多个原神 UID，且无法确定米游社当前角色"
    return ""


async def _update_panel_from_mys(event: MessageEvent) -> Any:
    from .mys_sync import sync_single_genshin_uid

    provider = _load_mys_provider()
    if provider is None:
        return (
            "米游社面板同步需要额外的登录态提供方。\n"
            f"请设置环境变量 {MYS_PROVIDER_ENV}=module:attr 指向一个已登录的"
            "米游社服务对象（需提供 load_user 与 MysAPI），"
            "配置后本命令即可使用。"
        )

    user = provider.load_user(event.user_id)
    if error := _mys_role_error(user):
        return error

    async def notify_captcha(link: str) -> None:
        await mys_update_panel.send(
            MessageSegment.reply(event.message_id)
            + "更新原神面板需要完成验证码，请在 3 分钟内打开链接：\n"
            + link
            + "\n验证完成后将自动继续更新"
        )

    api = provider.MysAPI()
    try:
        result = await sync_single_genshin_uid(
            event.user_id, user, api, notify_captcha
        )
    finally:
        await api.close()

    if result.status == "synced":
        player_info = PlayerInfo(result.uid)
        roles_list = player_info.get_roles_list()
        try:
            await check_artifact(event, player_info, roles_list, result.uid, True)
        except Exception as error:
            return (
                f"原神角色信息已更新，共更新 {result.role_count} 个角色；"
                f"圣遗物列表和榜单更新失败: {error}"
            )
        try:
            update_image = await draw_role_pic(result.uid, roles_list, player_info)
        except Exception as error:
            return (
                f"原神面板数据更新完成，共更新 {result.role_count} 个角色，"
                f"但更新图生成失败: {error}"
            )
        return update_image + (
            f"\n原神面板更新完成，共更新 {result.role_count} 个角色，"
            "已同步角色装备圣遗物及榜单"
        )
    if result.status == "failed":
        return f"原神面板更新失败: {result.error}"
    return "当前米游社账号无法确定唯一原神 UID"


@_matcher_handle(mys_update_panel)
async def _(event: MessageEvent) -> Outcome:
    message = await _update_panel_from_mys(event)
    return Outcome([_reply_finish(MessageSegment.reply(event.message_id) + message)])


@_matcher_handle(check)
async def _(event: MessageEvent) -> Outcome:
    # 与原实现保持一致：仅解析 UID，不做进一步处理。
    await get_msg_uid(event, check)
    return Outcome()


# ---------------------------------------------------------------------------
# 可选：更新检查
# ---------------------------------------------------------------------------
def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


async def get_update_info() -> str:
    try:
        body = await AsyncHttpx.get_content(
            config.genshin_role_info_changelog_url
        )
        match = re.search(r"\*\*\[v\d.\d.\d]((?:.|\n)*?)\*\*", body.decode("utf-8", "ignore"))
    except Exception as error:
        nonebot.logger.warning(f"{PLUGIN_NAME}插件获取更新内容失败: {error}")
        return ""
    return match.group(1).strip() if match else ""


async def _get_update_message() -> str | None:
    try:
        body = await AsyncHttpx.get_content(config.genshin_role_info_version_url)
        text = body.decode("utf-8", "ignore")
        # 兼容两种写法：PluginMetadata 的 version="x.y.z" 与
        # config.py 中的 PLUGIN_VERSION = "x.y.z"
        match = re.search(r'version="(\d+\.\d+\.\d+)"', text) or re.search(
            r'PLUGIN_VERSION\s*=\s*"(\d+\.\d+\.\d+)"', text
        )
    except Exception as error:
        nonebot.logger.warning(f"{PLUGIN_NAME}插件检查更新失败: {error}")
        return None
    if not match:
        nonebot.logger.warning(f"{PLUGIN_NAME}插件检查更新失败，远端版本号格式无效")
        return None
    latest_version = match.group(1)
    if _version_key(latest_version) <= _version_key(__plugin_version__):
        return None
    update_info = await get_update_info()
    return (
        f"检测到{PLUGIN_NAME}插件有更新"
        f"(当前V{__plugin_version__},最新V{latest_version})！请前往github下载！\n"
        f"本次更新内容如下:\n{update_info}"
    )


async def _notify_update_to_superusers() -> None:
    message = await _get_update_message()
    if not message:
        return
    bot = nonebot.get_bot()
    for admin in bot.config.superusers:
        try:
            await bot.send_private_msg(user_id=int(admin), message=message)
        except Exception as error:
            nonebot.logger.warning(f"{PLUGIN_NAME}: 通知超级用户失败: {error}")


@_matcher_handle(check_update)
async def _() -> Outcome:
    message = await _get_update_message()
    if message:
        return Outcome([Reply(message)])
    return Outcome()


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------
driver = get_driver()


@driver.on_startup
async def _init_data_dirs() -> None:
    path = ensure_data_dirs()
    nonebot.logger.info(f"{PLUGIN_NAME}: 用户数据目录 {path}")


@driver.on_shutdown
async def _close_http_client() -> None:
    await close_client()


if scheduler is not None and config.genshin_role_info_check_update:
    @driver.on_startup
    async def _schedule_update_check() -> None:
        scheduler.add_job(
            _notify_update_to_superusers,
            "cron",
            hour=random.randint(9, 22),
            minute=random.randint(0, 59),
            id="genshin_role_info",
            replace_existing=True,
        )
elif scheduler is None:
    nonebot.logger.warning(
        f"{PLUGIN_NAME}: 未安装 nonebot-plugin-apscheduler，已跳过每日更新检查调度。"
    )
