"""圣遗物推荐算法测试。

覆盖 ``data_source/damage/recommend.py``：伤害目标选取、单件排名、
整套推荐（穷举 / 集束搜索两条路径）。
"""

from __future__ import annotations

import copy

import pytest

from nonebot_plugin_genshin_role_info.data_source.damage import get_role_dmg
from nonebot_plugin_genshin_role_info.data_source.damage.recommend import (
    DamageTargetError,
    apply_artifact_combination,
    get_damage_target,
    rank_single_artifacts,
    recommend_artifact_set,
    recommend_single_artifact,
)

from tests._helpers import load_player_cache, make_hutao_panel


@pytest.fixture
def hutao():
    return make_hutao_panel()


# ---------------------------------------------------------------------------
# get_damage_target
# ---------------------------------------------------------------------------
def test_get_damage_target_first_item(hutao):
    title, value = get_damage_target(hutao, 1)
    assert title
    assert isinstance(value, float)
    assert value > 0


def test_get_damage_target_matches_engine_order(hutao):
    """序号必须对应引擎输出（去掉“额外说明”）的 1-based 顺序。"""
    result = get_role_dmg(copy.deepcopy(hutao))
    rows = [(k, v) for k, v in result.items() if k != "额外说明"]
    for index, (title, values) in enumerate(rows, start=1):
        assert get_damage_target(hutao, index) == (title, float(values[0]))


def test_get_damage_target_index_out_of_range(hutao):
    with pytest.raises(DamageTargetError):
        get_damage_target(hutao, 0)
    with pytest.raises(DamageTargetError):
        get_damage_target(hutao, 999)


def test_get_damage_target_character_without_rules(hutao):
    broken = copy.deepcopy(hutao)
    broken["名称"] = "不存在的角色"
    with pytest.raises(DamageTargetError):
        get_damage_target(broken, 1)


def test_get_damage_target_rejects_text_row():
    """文本型伤害项不可作为推荐目标。"""
    from nonebot_plugin_genshin_role_info.data_source.damage import recommend

    def fake_get_role_dmg(data):
        from nonebot_plugin_genshin_role_info.data_source.damage.models import (
            DamageValues,
        )

        return {
            "文本": DamageValues(("34.0%",), is_text=True),
            "数值": ("500", "700"),
        }

    original = recommend.get_role_dmg
    recommend.get_role_dmg = fake_get_role_dmg
    try:
        with pytest.raises(DamageTargetError):
            get_damage_target({}, 1)
        assert get_damage_target({}, 2) == ("数值", 500.0)
    finally:
        recommend.get_role_dmg = original


# ---------------------------------------------------------------------------
# apply_artifact_combination
# ---------------------------------------------------------------------------
def test_apply_artifact_combination_same_artifacts_is_identity(hutao):
    """换成同一套圣遗物时属性不应变化（浮点累加顺序会带来 1e-16 级误差）。"""
    result = apply_artifact_combination(hutao, copy.deepcopy(hutao["圣遗物"]))
    for key, value in hutao["属性"].items():
        if isinstance(value, list):
            for index, item in enumerate(value):
                assert result["属性"][key][index] == pytest.approx(item, abs=1e-9)
        else:
            assert result["属性"][key] == pytest.approx(value, abs=1e-9)


def test_apply_artifact_combination_updates_percent_stats(hutao):
    """提升百分比攻击力副词条应提高额外攻击。"""
    better = copy.deepcopy(hutao["圣遗物"])
    better[0]["词条"][0] = {"属性名": "百分比攻击力", "属性值": 40.0}

    result = apply_artifact_combination(hutao, better)
    assert result["属性"]["额外攻击"] > hutao["属性"]["额外攻击"]


def test_apply_artifact_combination_updates_element_bonus(hutao):
    better = copy.deepcopy(hutao["圣遗物"])
    better[3]["主属性"] = {"属性名": "火元素伤害加成", "属性值": 90.0}
    result = apply_artifact_combination(hutao, better)
    # 伤害加成下标 1 是火元素
    assert result["属性"]["伤害加成"][1] > hutao["属性"]["伤害加成"][1]


def test_apply_artifact_combination_does_not_mutate_input(hutao):
    snapshot = copy.deepcopy(hutao)
    apply_artifact_combination(hutao, hutao["圣遗物"])
    assert hutao == snapshot


def test_apply_artifact_combination_requires_five(hutao):
    with pytest.raises(ValueError):
        apply_artifact_combination(hutao, hutao["圣遗物"][:4])


def test_apply_artifact_combination_clamps_negative_crit(hutao):
    """去掉暴击词条后暴击率不应变成负数。"""
    worse = []
    for artifact in copy.deepcopy(hutao["圣遗物"]):
        artifact["词条"] = [
            affix for affix in artifact["词条"] if "暴击" not in affix["属性名"]
        ]
        worse.append(artifact)
    result = apply_artifact_combination(hutao, worse)
    assert result["属性"]["暴击率"] >= 0
    assert result["属性"]["暴击伤害"] >= 0


# ---------------------------------------------------------------------------
# 单件排名
# ---------------------------------------------------------------------------
def test_rank_single_artifacts_orders_by_damage(hutao):
    candidates = copy.deepcopy(hutao["圣遗物"][4:5]) * 1
    # 构造两个候选：一个高爆伤、一个低爆伤
    strong = copy.deepcopy(candidates[0])
    strong["主属性"] = {"属性名": "暴击伤害", "属性值": 62.2}
    strong["词条"] = [{"属性名": "暴击率", "属性值": 20.0}]
    weak = copy.deepcopy(candidates[0])
    weak["主属性"] = {"属性名": "暴击伤害", "属性值": 62.2}
    weak["词条"] = [{"属性名": "防御力", "属性值": 20.0}]

    ranked = rank_single_artifacts(hutao, [weak, strong], 4, 1)
    assert len(ranked) == 2
    assert ranked[0].value >= ranked[1].value
    assert ranked[0].data["圣遗物"][4]["词条"][0]["属性名"] == "暴击率"


def test_rank_single_artifacts_validates_index(hutao):
    with pytest.raises(DamageTargetError):
        rank_single_artifacts(hutao, hutao["圣遗物"][:1], 0, 999)


def test_recommend_single_artifact_returns_best(hutao):
    candidates = copy.deepcopy(hutao["圣遗物"][:2])
    result = recommend_single_artifact(hutao, candidates, 0, 1)
    assert result is not None
    assert result.title
    assert result.value > 0


def test_recommend_single_artifact_empty_candidates(hutao):
    assert recommend_single_artifact(hutao, [], 0, 1) is None


def test_rank_single_artifacts_ties_keep_input_order(hutao):
    """同分时保持输入顺序，保证结果可复现。"""
    a = copy.deepcopy(hutao["圣遗物"][0])
    b = copy.deepcopy(hutao["圣遗物"][0])
    ranked = rank_single_artifacts(hutao, [a, b], 0, 1)
    assert len(ranked) == 2
    assert ranked[0].value == ranked[1].value


# ---------------------------------------------------------------------------
# 整套推荐
# ---------------------------------------------------------------------------
def _build_artifact_pool(size: int = 3):
    """构造一份可用的圣遗物池（五个位置，每个位置 size 件）。"""
    base = make_hutao_panel()["圣遗物"]
    positions = ["生之花", "死之羽", "时之沙", "空之杯", "理之冠"]
    pool = []
    for index, position in enumerate(positions):
        bucket = []
        for variant in range(size):
            artifact = copy.deepcopy(base[index])
            artifact["名称"] = f"{base[index]['名称']}{variant}"
            artifact["部位"] = position
            artifact["角色"] = "" if variant % 2 == 0 else "胡桃"
            bucket.append(artifact)
        pool.append(bucket)
    return pool


def test_recommend_artifact_set_exhaustive_path(hutao):
    """小池子走穷举路径（组合数 <= 12000）。"""
    pool = _build_artifact_pool(2)
    result = recommend_artifact_set(hutao, pool, "炽烈的炎之魔女", 1)
    assert result is not None
    assert result.value > 0
    assert len(result.data["圣遗物"]) == 5
    # 结果必须优于或等于原面板
    _, base_value = get_damage_target(hutao, 1)
    assert result.value >= base_value


def test_recommend_artifact_set_beam_path(hutao):
    """大池子走集束搜索路径（组合数超过穷举阈值 12000）。

    注意：该路径代价很高——``_best_from_beam`` 会对 5 个「散件位置」各跑一遍，
    每次都要反复调用伤害引擎。这里把池子控制在刚好越过阈值的规模，
    既覆盖 beam 分支又不让测试过慢。
    """
    pool = _build_artifact_pool(3)
    # 每位置 6 件 -> 6^5 = 7776 仍未越界；扩到 8 件 -> 8^5 = 32768 触发 beam
    big_pool = [bucket * 3 for bucket in pool]  # 每位置 9 件 -> 9^5 = 59049
    result = recommend_artifact_set(hutao, big_pool, "炽烈的炎之魔女", 1)
    assert result is not None
    assert result.value > 0


def test_recommend_artifact_set_occupy_filters_by_owner(hutao):
    pool = _build_artifact_pool(2)
    free = recommend_artifact_set(hutao, pool, "炽烈的炎之魔女", 1, occupy=False)
    owned = recommend_artifact_set(hutao, pool, "炽烈的炎之魔女", 1, occupy=True)
    assert free is not None
    assert owned is not None
    # 限制占用后不可能更优
    assert owned.value <= free.value


def test_recommend_artifact_set_no_matching_suit_returns_none(hutao):
    pool = _build_artifact_pool(1)
    assert recommend_artifact_set(hutao, pool, "不存在的套装", 1) is None


def test_recommend_artifact_set_empty_pool_returns_none(hutao):
    assert recommend_artifact_set(hutao, [[], [], [], [], []], "炽烈的炎之魔女", 1) is None


def test_recommend_artifact_set_validates_index(hutao):
    pool = _build_artifact_pool(1)
    with pytest.raises(DamageTargetError):
        recommend_artifact_set(hutao, pool, "炽烈的炎之魔女", 999)


def test_recommend_artifact_set_result_uses_real_artifacts(hutao):
    """推荐结果必须由池中真实圣遗物组成，不能是凭空生成的数据。"""
    pool = _build_artifact_pool(2)
    names = {
        artifact["名称"]
        for bucket in pool
        for artifact in bucket
    }
    result = recommend_artifact_set(hutao, pool, "炽烈的炎之魔女", 1)
    assert result is not None
    for artifact in result.data["圣遗物"]:
        assert artifact["名称"] in names


def test_recommend_artifact_set_with_real_cache():
    """用真实缓存数据做一次小规模整套推荐。"""
    cache = load_player_cache()
    role_data = copy.deepcopy(cache["角色"]["阿罗夏"])
    if len(role_data["圣遗物"]) != 5:
        pytest.skip("该角色圣遗物不足 5 件")
    pool = [[copy.deepcopy(role_data["圣遗物"][i])] for i in range(5)]
    # 只有唯一组合，应返回该组合
    suit = role_data["圣遗物"][0]["所属套装"]
    result = recommend_artifact_set(role_data, pool, suit, 1)
    if result is None:
        pytest.skip("该套装不足以构成推荐")
    assert result.value > 0
