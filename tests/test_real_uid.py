"""真实 UID 数据的解析与渲染测试。

夹具 ``tests/fixtures/enka_346948884.json`` 是 UID **346948884** 的真实
Enka 响应（抓取后仅保留 ``playerInfo`` 与 ``avatarInfoList`` 两个字段）。
因此这些用例完全离线，却覆盖真实数据全链路：解析 → 落盘 → 评分 → 伤害。

在线集成测试见文件末尾的 ``integration`` 标记用例（默认跳过）。
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from nonebot_plugin_genshin_role_info.utils.card_utils import PlayerInfo, get_name_by_id
from nonebot_plugin_genshin_role_info.utils.http_utils import AsyncHttpx

from tests._helpers import (
    FIXTURES,
    REAL_UID,
    load_enka_response,
    load_player_cache,
)

# 夹具抓取时记录的真实信息
EXPECTED_NICKNAME = "終眇玥さま"
EXPECTED_LEVEL = 57
EXPECTED_ROLES = [
    "桑多涅",
    "哥伦比娅",
    "七七",
    "伊涅芙",
    "玛薇卡",
    "菲林斯",
    "砂糖",
    "阿罗夏",
    "迪奥娜",
]


# ---------------------------------------------------------------------------
# 夹具自身完整性
# ---------------------------------------------------------------------------
def test_fixture_files_exist():
    assert (FIXTURES / f"enka_{REAL_UID}.json").is_file()
    assert (FIXTURES / f"player_cache_{REAL_UID}.json").is_file()


def test_enka_fixture_shape():
    data = load_enka_response()
    assert set(data) == {"playerInfo", "avatarInfoList"}
    assert data["playerInfo"]["nickname"] == EXPECTED_NICKNAME
    assert data["playerInfo"]["level"] == EXPECTED_LEVEL
    assert len(data["avatarInfoList"]) == len(EXPECTED_ROLES)


# ---------------------------------------------------------------------------
# 解析（Enka 原始响应 → PlayerInfo）
# ---------------------------------------------------------------------------
def test_parse_real_enka_response(player_info_dir: Path, enka_fixture: dict):
    """用真实响应构建 PlayerInfo，全部角色必须解析成功。"""
    api = PlayerInfo(REAL_UID)
    api.set_player(enka_fixture["playerInfo"])

    parsed = []
    for role in enka_fixture["avatarInfoList"]:
        # 真实响应里必然能解析；任何失败都应让用例失败
        api.set_role(role)
        parsed.append(get_name_by_id(str(role["avatarId"])))

    assert parsed == EXPECTED_ROLES
    assert api.get_player_info()["昵称"] == EXPECTED_NICKNAME
    assert api.get_player_info()["等级"] == EXPECTED_LEVEL

    api.save()
    assert (player_info_dir / f"{REAL_UID}.json").is_file()


def test_parsed_role_structure(player_info_dir: Path, enka_fixture: dict):
    """解析出的角色数据必须包含渲染与计算所需的全部字段。"""
    api = PlayerInfo(REAL_UID)
    api.set_player(enka_fixture["playerInfo"])
    for role in enka_fixture["avatarInfoList"]:
        api.set_role(role)

    required_role_keys = {"名称", "等级", "元素", "天赋", "命座", "属性", "武器", "圣遗物", "更新时间"}
    required_prop_keys = {
        "基础生命", "额外生命", "基础攻击", "额外攻击", "基础防御", "额外防御",
        "暴击率", "暴击伤害", "元素精通", "元素充能效率", "伤害加成",
    }
    for name in EXPECTED_ROLES:
        data = api.get_roles_info(name)
        assert data is not None, f"{name} 未解析"
        assert required_role_keys <= set(data), f"{name} 缺少字段"
        assert required_prop_keys <= set(data["属性"]), f"{name} 属性缺失"
        assert len(data["天赋"]) == 3
        # 伤害加成固定为 8 个元素通道（物理 + 七元素）
        assert len(data["属性"]["伤害加成"]) == 8
        assert data["武器"]["名称"]
        # 每个角色都应带满 5 件圣遗物（展柜内角色）
        assert len(data["圣遗物"]) == 5


def test_artifact_substats_have_expected_fields(player_info_dir: Path, enka_fixture: dict):
    api = PlayerInfo(REAL_UID)
    api.set_player(enka_fixture["playerInfo"])
    for role in enka_fixture["avatarInfoList"]:
        api.set_role(role)

    for name in EXPECTED_ROLES:
        for artifact in api.get_roles_info(name)["圣遗物"]:
            assert artifact["部位"] in {"生之花", "死之羽", "时之沙", "空之杯", "理之冠"}
            assert artifact["主属性"]["属性名"]
            assert artifact["星级"] == 5
            assert 0 <= artifact["等级"] <= 20
            for affix in artifact["词条"]:
                assert affix["属性名"]
                assert "属性值" in affix


# ---------------------------------------------------------------------------
# 缓存夹具（解析后的快照）
# ---------------------------------------------------------------------------
def test_player_cache_fixture_roles():
    cache = load_player_cache()
    assert list(cache["角色"]) == EXPECTED_ROLES
    assert cache["玩家信息"]["昵称"] == EXPECTED_NICKNAME
    assert cache["圣遗物列表"] == [[], [], [], [], []]
    assert cache["圣遗物榜单"] == []


def test_player_info_loads_cache_fixture(player_info_dir: Path):
    cache = load_player_cache()
    (player_info_dir / f"{REAL_UID}.json").write_text(
        json.dumps(cache, ensure_ascii=False), encoding="utf-8"
    )
    api = PlayerInfo(REAL_UID)
    assert api.get_roles_list() == EXPECTED_ROLES
    assert api.get_player_info()["昵称"] == EXPECTED_NICKNAME


# ---------------------------------------------------------------------------
# 真实数据的伤害计算
# ---------------------------------------------------------------------------
def test_damage_for_every_real_role():
    """真实角色的伤害计算必须成功（至少返回可渲染的结果或不适用 None）。"""
    cache = load_player_cache()
    from nonebot_plugin_genshin_role_info.data_source.damage import get_role_dmg

    computed = []
    for name, role_data in cache["角色"].items():
        result = get_role_dmg(copy.deepcopy(role_data))
        if result is None:
            continue
        assert isinstance(result, dict)
        assert "额外说明" in result
        computed.append(name)
    # 上游规则覆盖 129 个角色，这 9 个都应有规则
    assert len(computed) == len(EXPECTED_ROLES), f"未计算出伤害的角色: {set(EXPECTED_ROLES) - set(computed)}"


def test_damage_values_are_positive_numbers():
    cache = load_player_cache()
    from nonebot_plugin_genshin_role_info.data_source.damage import get_role_dmg

    for name, role_data in cache["角色"].items():
        result = get_role_dmg(copy.deepcopy(role_data))
        assert result is not None, f"{name} 无伤害结果"
        for title, values in result.items():
            if title == "额外说明" or getattr(values, "is_text", False):
                continue
            assert len(values) in (1, 2)
            assert float(values[0]) > 0, f"{name}/{title} 期望伤害非正"
            if len(values) == 2:
                assert float(values[1]) >= float(values[0]), f"{name}/{title} 暴击应不低于期望"


# ---------------------------------------------------------------------------
# 在线集成测试（默认跳过）
# ---------------------------------------------------------------------------
@pytest.mark.integration
async def test_live_enka_query(real_uid: str):
    """在线查询真实 UID（需要网络）。

    运行方式::

        pytest -m integration tests/test_real_uid.py
    """
    content = await AsyncHttpx.get_content(
        f"https://enka.network/api/uid/{real_uid}",
        headers={"User-Agent": "Miao-Plugin/3.0"},
        timeout=60,
    )
    data = json.loads(content.decode("utf-8"))
    assert "playerInfo" in data
    assert data["playerInfo"]["nickname"] == EXPECTED_NICKNAME
    # 橱窗内容可能随玩家操作变化，因此只断言基本结构
    assert isinstance(data.get("avatarInfoList"), list)


@pytest.mark.integration
async def test_live_fetch_and_parse(real_uid: str, player_info_dir: Path):
    """在线抓取并解析到缓存目录（不渲染图片）。"""
    content = await AsyncHttpx.get_content(
        f"https://enka.network/api/uid/{real_uid}",
        headers={"User-Agent": "Miao-Plugin/3.0"},
        timeout=60,
    )
    data = json.loads(content.decode("utf-8"))
    api = PlayerInfo(real_uid)
    api.set_player(data["playerInfo"])
    for role in data.get("avatarInfoList") or []:
        api.set_role(role)
    api.save()
    assert api.get_roles_list(), "在线数据未解析出任何角色"
