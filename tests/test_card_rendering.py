"""卡片渲染与消息输出测试。

渲染依赖网络图标（enka.network）。测试中把 HTTP 层替换为**读取本地真实素材**，
因此既不访问网络，又能覆盖真实解码/缩放/合成路径。

另外包含一组「非回归」断言：用真实 UID 的缓存数据渲染，检查输出尺寸与
关键宽度阈值，防止布局被破坏。
"""

from __future__ import annotations

import copy
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from nonebot_plugin_genshin_role_info import paths
from nonebot_plugin_genshin_role_info.data_source.damage import get_role_dmg
from nonebot_plugin_genshin_role_info.data_source.damage.render import draw_dmg_pic
from nonebot_plugin_genshin_role_info.data_source.draw_artifact_card import (
    draw_artifact_card,
    draw_qq_logo_mask,
)
from nonebot_plugin_genshin_role_info.data_source.draw_role_card import (
    draw_role_card,
)
from nonebot_plugin_genshin_role_info.data_source.draw_role_rank_card import (
    draw_role_rank_card,
)
from nonebot_plugin_genshin_role_info.data_source.draw_update_card import draw_role_pic
from nonebot_plugin_genshin_role_info.utils.card_utils import PlayerInfo
from nonebot_plugin_genshin_role_info.utils.image_utils import (
    draw_center_text,
    draw_right_text,
    draw_text_with_safe_advance,
    image_build,
    load_image,
)

from tests._helpers import REAL_UID, load_player_cache

# 用于替代网络图标的本地真实素材
LOCAL_ICON = Path(paths.other_path) / "star5.png"


@pytest.fixture
def offline_network(monkeypatch):
    """把图标下载替换为读取本地真实素材。

    渲染路径实际调用的是 ``AsyncHttpx.download_file``（经
    ``download_file_checked`` 校验后落盘），而不是 ``get_content``，
    因此这里必须替换 ``download_file`` 才能真正离线。
    """
    from nonebot_plugin_genshin_role_info.utils import http_utils

    payload = LOCAL_ICON.read_bytes()
    calls: list[str] = []

    async def fake_download_file(url, path, **kwargs):
        urls = [url] if isinstance(url, str) else list(url)
        calls.append(urls[0])
        # 模拟真实下载：写入临时文件，交由调用方做校验与原子替换
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(payload)
        return True

    monkeypatch.setattr(http_utils.AsyncHttpx, "download_file", fake_download_file)
    return calls


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    """把图标缓存与玩家数据目录指向临时目录，避免用例之间互相命中缓存。

    注意：绘制模块（``draw_role_card`` 等）在导入时用
    ``from ..utils.card_utils import weapon_path`` 把路径**绑定为模块级常量**，
    因此只替换 ``card_utils`` 不够——必须同时替换每个已绑定该常量的消费模块，
    否则渲染仍会写入真实数据目录（那里已有缓存，于是不再触发下载）。
    """
    from nonebot_plugin_genshin_role_info.data_source import (
        draw_artifact_card,
        draw_recommend_card,
        draw_role_card,
        draw_role_rank_card,
        draw_update_card,
    )
    from nonebot_plugin_genshin_role_info.utils import card_utils

    writable = {
        "char_pic_path": tmp_path / "character",
        "avatar_path": tmp_path / "avatar",
        "skill_path": tmp_path / "skill",
        "talent_path": tmp_path / "talent",
        "weapon_path": tmp_path / "weapon",
        "reli_path": tmp_path / "reli",
        "qq_logo_path": tmp_path / "qq_logo",
        "player_info_path": tmp_path / "player_info",
        "group_info_path": tmp_path / "group_info",
        "data_res_path": tmp_path,
    }
    # 内置只读资源仍指向插件包
    readonly = {
        "json_path": paths.json_path,
        "bg_path": paths.bg_path,
        "other_path": paths.other_path,
        "outline_path": paths.outline_path,
        "regoin_path": paths.regoin_path,
        "font_path": paths.font_path,
    }
    for directory in writable.values():
        directory.mkdir(parents=True, exist_ok=True)

    replacements = {
        **{name: str(value) for name, value in writable.items()},
        **readonly,
    }
    for name, value in replacements.items():
        monkeypatch.setattr(card_utils, name, value)

    for module in (
        draw_role_card,
        draw_artifact_card,
        draw_recommend_card,
        draw_role_rank_card,
        draw_update_card,
    ):
        for name, value in replacements.items():
            if hasattr(module, name):
                monkeypatch.setattr(module, name, value)

    import nonebot_plugin_genshin_role_info as plugin_module

    for name in ("player_info_path", "group_info_path"):
        if hasattr(plugin_module, name):
            monkeypatch.setattr(plugin_module, name, replacements[name])

    return tmp_path


@pytest.fixture
def player_info(isolated_data_dir):
    """把真实 UID 缓存写入**已隔离**的目录并返回 PlayerInfo。

    依赖 ``isolated_data_dir`` 是刻意的：路径必须在构造 PlayerInfo 之前
    就已替换，否则会读到共享目录中的旧缓存。
    """
    import json

    from nonebot_plugin_genshin_role_info.utils.card_utils import player_info_path

    cache = load_player_cache()
    target = Path(player_info_path) / f"{REAL_UID}.json"
    target.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return PlayerInfo(REAL_UID)


# ---------------------------------------------------------------------------
# 基础绘图工具
# ---------------------------------------------------------------------------
def test_image_build_returns_jpeg_segment_for_rgb():
    segment = image_build(Image.new("RGB", (16, 16), "red"), quality=100)
    assert segment.type == "image"


def test_image_build_returns_png_segment_for_rgba():
    segment = image_build(Image.new("RGBA", (16, 16), (255, 0, 0, 128)))
    assert segment.type == "image"


def test_image_build_accepts_path():
    segment = image_build(LOCAL_ICON, quality=80)
    assert segment.type == "image"


def test_image_build_resizes_and_converts():
    img = Image.new("RGB", (100, 50), "blue")
    segment = image_build(img, size=(20, 10))
    assert segment.type == "image"


def test_load_image_with_size_and_mode():
    img = load_image(LOCAL_ICON, size=(32, 32), mode="RGBA")
    assert img.size == (32, 32)
    assert img.mode == "RGBA"


def test_load_image_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        load_image(Path(paths.other_path) / "不存在的图.png")


def test_draw_helpers_do_not_crash_with_zero_width_glyphs():
    """部分角色名含零宽字形，居中/左对齐绘制必须有兜底。"""
    img = Image.new("RGBA", (400, 120), (0, 0, 0, 0))
    from PIL import ImageDraw

    from nonebot_plugin_genshin_role_info.utils.card_utils import get_font

    draw = ImageDraw.Draw(img)
    font = get_font(24)
    draw_right_text(draw, "测试", 380, 10, "white", font)
    draw_text_with_safe_advance(draw, (10, 50), "奥黛塔\u200b测试", fill="white", font=font)
    draw_center_text(draw, "居中文本", 0, 400, 80, "white", font)


# ---------------------------------------------------------------------------
# 伤害表渲染
# ---------------------------------------------------------------------------
def test_draw_dmg_pic_with_real_damage_data():
    from tests._helpers import make_hutao_panel

    damage = get_role_dmg(make_hutao_panel())
    assert damage is not None
    image = draw_dmg_pic(damage)
    assert isinstance(image, Image.Image)
    # 宽度固定为 948（引擎与卡片布局的约定）
    assert image.size[0] == 948
    assert image.size[1] > 100


def test_draw_dmg_pic_handles_missing_buff_notes():
    """没有“额外说明”时也必须能出图。"""
    image = draw_dmg_pic({"条目A": ("100", "200")})
    assert isinstance(image, Image.Image)
    assert image.size[0] == 948


def test_draw_dmg_pic_handles_text_rows():
    from nonebot_plugin_genshin_role_info.data_source.damage.models import DamageValues

    image = draw_dmg_pic(
        {
            "文本行": DamageValues(("34.0%",), is_text=True),
            "数值行": ("500", "700"),
            "额外说明": ("某套装：伤害提高20%",),
        }
    )
    assert isinstance(image, Image.Image)


def test_damage_render_uses_bundled_assets(monkeypatch):
    """render.py 的 OTHER_PATH/FONT_PATH 必须指向 res/ 下的真实目录。

    这是迁移过程中真实出现过的路径错误（漏掉 res/ 一级），此处做定点回归。
    """
    from nonebot_plugin_genshin_role_info.data_source.damage import render

    assert Path(render.OTHER_PATH).is_dir(), f"{render.OTHER_PATH} 不存在"
    assert Path(render.FONT_PATH).is_dir(), f"{render.FONT_PATH} 不存在"
    assert Path(render.OTHER_PATH) == Path(paths.other_path)
    assert Path(render.FONT_PATH) == Path(paths.font_path)


# ---------------------------------------------------------------------------
# 角色面板卡
# ---------------------------------------------------------------------------
async def test_draw_role_card_with_real_cache(
    offline_network, player_info, isolated_data_dir
):
    """首次渲染：必须触发图标下载并缓存到数据目录。"""
    role_data = player_info.get_roles_info("阿罗夏")
    image, score = await draw_role_card(
        REAL_UID, role_data, player_info, "4.3.13", only_cal=False
    )
    assert isinstance(image, Image.Image)
    assert image.size[0] == 1080
    # 有伤害表时高度会大于基础 1920
    assert image.size[1] >= 1920
    assert score != ""
    # 隔离目录后必须真正走过取图路径
    assert offline_network, "未触发图标下载"
    cached = list((isolated_data_dir / "weapon").glob("*.png"))
    cached += list((isolated_data_dir / "reli").glob("*.png"))
    cached += list((isolated_data_dir / "character").glob("*.png"))
    assert cached, "图标未缓存到数据目录"


async def test_draw_role_card_reuses_cached_icons(player_info, isolated_data_dir):
    """第二次渲染应命中本地缓存，不再发起下载。"""
    from nonebot_plugin_genshin_role_info.utils import http_utils

    downloads: list[str] = []
    payload = LOCAL_ICON.read_bytes()
    real_download = http_utils.AsyncHttpx.download_file

    async def counting_download(url, path, **kwargs):
        urls = [url] if isinstance(url, str) else list(url)
        downloads.append(urls[0])
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(payload)
        return True

    http_utils.AsyncHttpx.download_file = counting_download
    try:
        role_data = copy.deepcopy(player_info.get_roles_info("阿罗夏"))
        await draw_role_card(REAL_UID, role_data, player_info, "4.3.13", only_cal=False)
        first = len(downloads)

        role_data = copy.deepcopy(player_info.get_roles_info("阿罗夏"))
        await draw_role_card(REAL_UID, role_data, player_info, "4.3.13", only_cal=False)
        second = len(downloads) - first
    finally:
        http_utils.AsyncHttpx.download_file = real_download

    assert first > 0, "首次渲染未下载图标"
    assert second == 0, f"第二次渲染仍发起了 {second} 次下载（缓存失效）"


async def test_draw_role_card_only_cal_skips_rendering(offline_network, player_info):
    """only_cal=True 用于重建评分缓存，不应绘制完整卡片。"""
    role_data = copy.deepcopy(player_info.get_roles_info("阿罗夏"))
    image, score = await draw_role_card(
        REAL_UID, role_data, player_info, "4.3.13", only_cal=True
    )
    assert isinstance(image, Image.Image)
    assert image.size == (1080, 1920)
    # 评分仍然被计算出来
    assert float(str(score).rstrip("*")) >= 0


async def test_draw_role_card_updates_artifact_ranking(offline_network, player_info):
    """渲染时应把本角色圣遗物写入榜单缓存。"""
    player_info.data["圣遗物榜单"] = []
    role_data = copy.deepcopy(player_info.get_roles_info("阿罗夏"))
    await draw_role_card(REAL_UID, role_data, player_info, "4.3.13", only_cal=False)
    assert len(player_info.data["圣遗物榜单"]) > 0
    entry = player_info.data["圣遗物榜单"][0]
    for key in ("名称", "评分", "评级", "主属性", "副属性", "角色"):
        assert key in entry


async def test_draw_role_card_scores_are_bounded(
    offline_network, player_info, isolated_data_dir
):
    """全角色评分应为有限数值，且评级属于已知集合。"""
    valid_grades = {"D", "C", "B", "A", "S", "SS", "SSS", "ACE", "ACE*"}
    for name in player_info.get_roles_list():
        player_info.data["圣遗物榜单"] = []
        role_data = copy.deepcopy(player_info.get_roles_info(name))
        _, score = await draw_role_card(
            REAL_UID, role_data, player_info, "4.3.13", only_cal=False
        )
        value = float(str(score).rstrip("*"))
        assert value == value, f"{name} 评分为 NaN"  # NaN 自比较为 False
        assert value >= 0
        for entry in player_info.data["圣遗物榜单"]:
            assert entry["评级"] in valid_grades


# ---------------------------------------------------------------------------
# 更新图
# ---------------------------------------------------------------------------
async def test_draw_role_pic_with_real_cache(offline_network, player_info):
    roles = player_info.get_roles_list()
    segment = await draw_role_pic(REAL_UID, roles, player_info)
    assert segment.type == "image"


async def test_draw_role_pic_accepts_role_list(offline_network, player_info):
    """role_dict 既可用角色名列表，也可用字典。"""
    roles = player_info.get_roles_list()
    as_dict = {name: True for name in roles}
    segment = await draw_role_pic(REAL_UID, as_dict, player_info)
    assert segment.type == "image"


# ---------------------------------------------------------------------------
# 圣遗物榜单卡
# ---------------------------------------------------------------------------
async def test_draw_artifact_card_uid_mode(offline_network, player_info):
    player_info.data["圣遗物榜单"] = []
    for name in player_info.get_roles_list():
        role_data = copy.deepcopy(player_info.get_roles_info(name))
        await draw_role_card(REAL_UID, role_data, player_info, "4.3.13", only_cal=True)
    ranking = player_info.data["圣遗物榜单"]
    assert ranking

    segment, text = await draw_artifact_card(
        "圣遗物榜单",
        None,
        REAL_UID,
        ranking,
        player_info.data["大毕业圣遗物"],
        player_info.data["小毕业圣遗物"],
        "4.3.13",
    )
    assert segment.type == "image"
    assert "大毕业圣遗物" in text
    assert "最高评分为" in text


async def test_draw_artifact_card_group_mode(offline_network):
    group_ranking = [
        {
            "名称": "宗室之花",
            "图标": "UI_RelicIcon_15008_4",
            "星级": 5,
            "角色": "阿罗夏",
            "评分": 51.1,
            "评级": "ACE",
            "等级": 20,
            "主属性": {"属性名": "生命值", "属性值": 4780},
            "副属性": [
                {"属性名": "暴击率", "属性值": "+11.3%", "强化次数": "", "颜色": "white"},
                {"属性名": "元素充能效率", "属性值": "+12.3%", "强化次数": "", "颜色": "white"},
            ],
            "QQ": 10001,
        }
    ]
    segment, text = await draw_artifact_card(
        "群圣遗物榜单", None, 12345, group_ranking, None, None, "4.3.13", 1
    )
    assert segment.type == "image"
    # 群模式不返回文本统计
    assert text == ""


async def test_draw_artifact_card_damage_value_mode(offline_network):
    """带“伤害值”的条目在评分位置显示伤害而非评级。"""
    ranking = [
        {
            "名称": "宗室之花",
            "图标": "UI_RelicIcon_15008_4",
            "星级": 5,
            "角色": "阿罗夏",
            "评分": 12345.0,
            "评级": "ACE",
            "伤害值": 12345.0,
            "等级": 20,
            "主属性": {"属性名": "生命值", "属性值": 4780},
            "副属性": [],
            "QQ": 1,
        }
    ]
    segment, _ = await draw_artifact_card(
        "推荐", None, REAL_UID, ranking, 0, 0, "4.3.13", 1
    )
    assert segment.type == "image"


# ---------------------------------------------------------------------------
# 群排行卡
# ---------------------------------------------------------------------------
async def test_draw_role_rank_card(offline_network, player_info):
    from nonebot_plugin_genshin_role_info.utils.rank_utils import build_rank_entry

    role_data = copy.deepcopy(player_info.get_roles_info("阿罗夏"))
    # 评分是渲染阶段写入缓存的，夹具来自纯解析结果，因此这里显式补上
    role_data["评分"] = 100.0
    entries = [
        build_rank_entry(10001 + i, f"用户{i}", role_data, "评分")
        for i in range(5)
    ]
    for index, entry in enumerate(entries):
        entry["value"] = 100.0 - index

    image = await draw_role_rank_card(
        "阿罗夏评分排行", "阿罗夏", 12345, entries, "4.3.13", "评分"
    )
    assert isinstance(image, Image.Image)
    assert image.mode == "RGB"
    assert image.size[0] > 0 and image.size[1] > 0


async def test_draw_role_rank_card_damage_metric(offline_network, player_info):
    from nonebot_plugin_genshin_role_info.utils.rank_utils import build_rank_entry

    role_data = copy.deepcopy(player_info.get_roles_info("阿罗夏"))
    role_data["评分"] = 100.0
    entry = build_rank_entry(10001, "用户", role_data, "伤害", {"条目A": ("1000", "2000")}, 1)
    image = await draw_role_rank_card(
        "阿罗夏伤害排行1", "阿罗夏", 12345, [entry], "4.3.13", "伤害"
    )
    assert isinstance(image, Image.Image)


# ---------------------------------------------------------------------------
# QQ 头像遮罩
# ---------------------------------------------------------------------------
async def test_draw_qq_logo_mask(offline_network):
    mask_bottom = load_image(
        path=f"{paths.other_path}/底遮罩.png", crop=(707, 936, 1024, 1370)
    )
    image = await draw_qq_logo_mask({"QQ": 10001}, mask_bottom)
    assert isinstance(image, Image.Image)
    assert image.size == mask_bottom.size
