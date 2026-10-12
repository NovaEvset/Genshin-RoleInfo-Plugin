"""圣遗物评分算法测试（Miao-Plugin 兼容）。

本模块测试 ``utils/artifact_utils.py`` 的评分体系：评级分档、强化次数推算、
有效词条解析、单件评分端到端。

所有期望值均为**实际运行本实现得到**并已人工核对公式的结果（参见
``docs/TEST_REPORT.md`` 的方法说明）。
"""

from __future__ import annotations

import copy

import pytest

from nonebot_plugin_genshin_role_info.utils import artifact_utils as au
from nonebot_plugin_genshin_role_info.utils.card_utils import role_info_json


# ---------------------------------------------------------------------------
# 测试用面板
# ---------------------------------------------------------------------------
def make_ganyu_panel() -> dict:
    """甘雨 + 冰风迷途的勇士四件套的确定性面板。"""
    return {
        "名称": "甘雨",
        "元素": "冰",
        "武器": {
            "名称": "阿莫斯之弓",
            "副属性": {"属性名": "百分比攻击力", "属性值": 49.6},
        },
        "圣遗物": [
            {
                "所属套装": "冰风迷途的勇士",
                "图标": "UI_RelicIcon_15001_66",
                "等级": 20,
                "星级": 5,
                "主属性": {"属性名": "生命值", "属性值": 4780},
                "词条": [
                    {"属性名": "暴击率", "属性值": 10.5},
                    {"属性名": "暴击伤害", "属性值": 21.8},
                    {"属性名": "百分比攻击力", "属性值": 9.9},
                    {"属性名": "元素充能效率", "属性值": 6.5},
                ],
            },
            {
                "所属套装": "冰风迷途的勇士",
                "图标": "UI_RelicIcon_15001_28",
                "等级": 20,
                "星级": 5,
                "主属性": {"属性名": "攻击力", "属性值": 311},
                "词条": [
                    {"属性名": "暴击率", "属性值": 7.0},
                    {"属性名": "暴击伤害", "属性值": 28.0},
                    {"属性名": "百分比攻击力", "属性值": 5.8},
                    {"属性名": "元素充能效率", "属性值": 6.5},
                ],
            },
            {
                "所属套装": "冰风迷途的勇士",
                "图标": "UI_RelicIcon_15001_88",
                "等级": 20,
                "星级": 5,
                "主属性": {"属性名": "百分比攻击力", "属性值": 46.6},
                "词条": [
                    {"属性名": "暴击率", "属性值": 10.5},
                    {"属性名": "暴击伤害", "属性值": 14.0},
                    {"属性名": "元素精通", "属性值": 63},
                    {"属性名": "元素充能效率", "属性值": 5.2},
                ],
            },
            {
                "所属套装": "冰风迷途的勇士",
                "图标": "UI_RelicIcon_15001_43",
                "等级": 20,
                "星级": 5,
                "主属性": {"属性名": "冰元素伤害加成", "属性值": 46.6},
                "词条": [
                    {"属性名": "暴击伤害", "属性值": 26.4},
                    {"属性名": "暴击率", "属性值": 3.5},
                    {"属性名": "百分比攻击力", "属性值": 9.9},
                    {"属性名": "元素精通", "属性值": 40},
                ],
            },
            {
                "所属套装": "冰风迷途的勇士",
                "图标": "UI_RelicIcon_15001_13",
                "等级": 20,
                "星级": 5,
                "主属性": {"属性名": "暴击伤害", "属性值": 62.2},
                "词条": [
                    {"属性名": "暴击率", "属性值": 9.3},
                    {"属性名": "百分比攻击力", "属性值": 9.9},
                    {"属性名": "元素精通", "属性值": 42},
                    {"属性名": "元素充能效率", "属性值": 5.2},
                ],
            },
        ],
    }


# ---------------------------------------------------------------------------
# 评级分档
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "score,expected",
    [
        (0, "D"),
        (6.99, "D"),
        (7, "C"),
        (13.99, "C"),
        (14, "B"),
        (20.99, "B"),
        (21, "A"),
        (27.99, "A"),
        (28, "S"),
        (34.99, "S"),
        (35, "SS"),
        (41.99, "SS"),
        (42, "SSS"),
        (48.99, "SSS"),
        (49, "ACE"),
        (55.99, "ACE"),
        (56, "ACE*"),
        (100, "ACE*"),
    ],
)
def test_get_mark_class_boundaries(score, expected):
    """9 档评级的分界点。"""
    assert au.get_mark_class(score) == expected


# ---------------------------------------------------------------------------
# 强化次数标记
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "count,expected",
    [(1, "¹"), (2, "²"), (3, "³"), (4, "⁴"), (5, "⁵")],
)
def test_get_upgrade_count_mark_known(count, expected):
    assert au.get_upgrade_count_mark(count) == expected


@pytest.mark.parametrize("count", [0, 6, -1, None, "2"])
def test_get_upgrade_count_mark_unknown_returns_empty(count):
    """越界或非整数返回空串（注意字符串 "2" 不视为 2）。"""
    assert au.get_upgrade_count_mark(count) == ""


# ---------------------------------------------------------------------------
# 强化次数推算
# ---------------------------------------------------------------------------
def test_get_artifact_upgrade_counts_prefers_explicit_times():
    artifact = {
        "等级": 20,
        "词条": [
            {"属性名": "暴击率", "属性值": 10.5, "强化次数": 3},
            {"属性名": "暴击伤害", "属性值": 21.8, "强化次数": 2},
        ],
    }
    assert au.get_artifact_upgrade_counts(artifact) == [3, 2]


def test_get_artifact_upgrade_counts_clamps_explicit_times():
    artifact = {
        "等级": 20,
        "词条": [
            {"属性名": "暴击率", "属性值": 10.5, "强化次数": -2},
            {"属性名": "暴击伤害", "属性值": 21.8, "强化次数": 99},
        ],
    }
    assert au.get_artifact_upgrade_counts(artifact) == [0, 5]


def test_get_artifact_upgrade_counts_derives_from_values():
    """缺少强化次数时按词条数值推算（结果与等级上限有关）。"""
    artifact = {
        "等级": 20,
        "词条": [
            {"属性名": "暴击率", "属性值": 10.5},
            {"属性名": "暴击伤害", "属性值": 21.8},
            {"属性名": "百分比攻击力", "属性值": 9.9},
            {"属性名": "元素精通", "属性值": 42},
        ],
    }
    counts = au.get_artifact_upgrade_counts(artifact)
    assert len(counts) == 4
    assert all(0 <= count <= 5 for count in counts)
    # 等级 20 允许最多 5 次强化
    assert sum(counts) <= 5


def test_get_artifact_upgrade_counts_zero_value():
    artifact = {"等级": 20, "词条": [{"属性名": "暴击率", "属性值": 0}]}
    assert au.get_artifact_upgrade_counts(artifact) == [0]


def test_get_artifact_upgrade_counts_level0_artifact():
    artifact = {
        "等级": 0,
        "词条": [{"属性名": "暴击率", "属性值": 3.5}],
    }
    assert au.get_artifact_upgrade_counts(artifact) == [0]


def test_get_artifact_upgrade_counts_fill_ambiguous():
    """fill_ambiguous 会把歧义项补足到等级上限。"""
    artifact = {
        "等级": 20,
        "词条": [
            {"属性名": "暴击率", "属性值": 3.5},
            {"属性名": "暴击伤害", "属性值": 7.8},
        ],
    }
    plain = au.get_artifact_upgrade_counts(artifact)
    filled = au.get_artifact_upgrade_counts(copy.deepcopy(artifact), fill_ambiguous=True)
    assert sum(filled) >= sum(plain)


# ---------------------------------------------------------------------------
# 有效词条解析
# ---------------------------------------------------------------------------
def test_get_effective_ganyu():
    effective, name = au.get_effective(make_ganyu_panel())
    assert name == "甘雨"
    assert effective["暴击率"] == 100
    assert effective["暴击伤害"] == 100
    assert effective["元素伤害加成"] == 100
    assert effective["百分比攻击力"] == 75


def test_get_effective_unknown_role_returns_fallback():
    """score.json 里没有的角色应返回固定兜底权重。"""
    data = {
        "名称": "不存在的角色",
        "元素": "火",
        "武器": {"副属性": {"属性名": "百分比攻击力", "属性值": 0}},
        "圣遗物": copy.deepcopy(make_ganyu_panel()["圣遗物"]),
    }
    effective, name = au.get_effective(data)
    assert name == "不存在的角色"
    assert effective == {
        "百分比攻击力": 75,
        "暴击率": 100,
        "暴击伤害": 100,
        "元素伤害加成": 100,
        "物理伤害加成": 100,
    }


def test_get_effective_missing_artifacts_key_raises():
    """缺少 ``圣遗物`` 键会抛 KeyError。

    ``get_effective`` 的 try 块从第 295 行才开始，而 ``data["圣遗物"]`` 在
    第 286 行读取，因此该缺失**不会**被兜底捕获。上游数据总是由
    ``PlayerInfo`` 生成并保证包含该键，故正常流程不会触达。
    """
    data = {"名称": "甘雨", "元素": "冰", "武器": {"副属性": {"属性名": "百分比攻击力", "属性值": 0}}}
    with pytest.raises(KeyError):
        au.get_effective(data)


def test_get_effective_degrades_on_malformed_body():
    """结构齐全但内部字段异常时，由 try/except 兜底为固定权重。"""
    data = {
        "名称": "甘雨",
        "元素": "冰",
        # 缺少 武器，会在 try 块内触发 KeyError 并被兜底
        "圣遗物": copy.deepcopy(make_ganyu_panel()["圣遗物"]),
    }
    effective, name = au.get_effective(data)
    assert effective == {
        "百分比攻击力": 75,
        "暴击率": 100,
        "暴击伤害": 100,
        "元素伤害加成": 100,
        "物理伤害加成": 100,
    }
    # 兜底路径不追加档位后缀
    assert name == "甘雨"


def test_get_effective_traveler_by_element():
    """荧/空 按元素重映射到对应主角。"""
    base = make_ganyu_panel()
    base["名称"] = "荧"
    base["元素"] = "火"
    effective, name = au.get_effective(base)
    assert name == "火主"
    assert isinstance(effective, dict)

    base["元素"] = "草"
    _, name = au.get_effective(base)
    assert name == "草主"


def test_check_effective_substring_matching():
    """check_effective 使用子串匹配，且三族属性互相视为有效。"""
    assert au.check_effective("暴击率", {"暴击率": 100}) is True
    assert au.check_effective("百分比攻击力", {"百分比攻击力": 75}) is True
    # 攻击力族：'攻击力' 是 '百分比攻击力' 的子串关系
    assert au.check_effective("攻击力", {"百分比攻击力": 75}) is True
    assert au.check_effective("元素充能效率", {"暴击率": 100}) is False


# ---------------------------------------------------------------------------
# 套装识别
# ---------------------------------------------------------------------------
def test_get_artifact_suit_four_piece():
    suit = au.get_artifact_suit(make_ganyu_panel()["圣遗物"])
    assert suit
    assert suit[0][0] == "冰风迷途的勇士"


def test_get_artifact_suit_two_plus_two():
    artifacts = make_ganyu_panel()["圣遗物"]
    for artifact in artifacts[:2]:
        artifact["所属套装"] = "角斗士的终幕礼"
    suit = au.get_artifact_suit(artifacts)
    names = {entry[0] for entry in suit}
    assert "角斗士的终幕礼" in names


def test_get_artifact_suit_empty():
    assert au.get_artifact_suit([]) == []


def test_get_artifact_suit_ignores_incomplete_set():
    """不足 2 件不构成套装。"""
    artifacts = make_ganyu_panel()["圣遗物"][:1]
    assert au.get_artifact_suit(artifacts) == []


# ---------------------------------------------------------------------------
# get_miao_score
# ---------------------------------------------------------------------------
def test_get_miao_score_mutates_argument_and_returns_three_values():
    """get_miao_score 会就地修改传入的权重字典（源实现行为，不可依赖调用方副本）。"""
    effective = {"百分比攻击力": 75, "暴击率": 100, "暴击伤害": 100}
    base_info = dict(role_info_json["甘雨"]["属性"])
    result = au.get_miao_score(effective, base_info)
    assert len(result) == 3
    affix_weight, point_mark, max_mark = result
    # 攻击力被追加进权重（就地修改）
    assert "攻击力" in effective
    assert "攻击力" in affix_weight
    assert set(max_mark) == {"0", "1", "2", "3", "4"}


def test_get_miao_score_max_mark_positions():
    """花/羽没有主词条（main=0），但仍计入副词条满分。"""
    effective = {"暴击率": 100, "暴击伤害": 100, "百分比攻击力": 75}
    _, _, max_mark = au.get_miao_score(effective, dict(role_info_json["甘雨"]["属性"]))
    assert max_mark["0"]["main"] == 0
    assert max_mark["1"]["main"] == 0
    assert max_mark["2"]["main"] > 0
    # 花/羽无主词条，但 total 由有效副词条权重构成，不应为 0
    assert max_mark["0"]["total"] > 0
    assert max_mark["1"]["total"] > 0


# ---------------------------------------------------------------------------
# get_artifact_score 端到端
# ---------------------------------------------------------------------------
def _score_all(data: dict):
    effective, _ = au.get_effective(copy.deepcopy(data))
    _, point_mark, max_mark = au.get_miao_score(
        copy.deepcopy(effective), copy.deepcopy(role_info_json[data["名称"]]["属性"])
    )
    results = []
    for index, artifact in enumerate(data["圣遗物"]):
        results.append(
            au.get_artifact_score(
                point_mark, max_mark, artifact, data["元素"], index, data["名称"]
            )
        )
    return results


def test_artifact_score_end_to_end_ganyu():
    """甘雨四件套的端到端评分（数值为实跑结果，公式见 docs/TEST_REPORT.md）。"""
    results = _score_all(make_ganyu_panel())
    assert len(results) == 5
    grades = [grade for grade, _, _ in results]
    assert all(grade in {"D", "C", "B", "A", "S", "SS", "SSS", "ACE", "ACE*"} for grade in grades)

    # 花（生命值主词条 + 4 条有效副词条）应达到 ACE 档
    assert grades[0] == "ACE"
    assert results[0][1] == pytest.approx(55.78998019155493, abs=1e-9)
    assert results[0][2] == [2, 2, 1, 0]

    # 总分评级
    total = sum(round(score, 1) for _, score, _ in results)
    assert au.get_mark_class(total / 5) == "SSS"


def test_artifact_score_is_independent_of_level():
    """评分公式不含圣遗物等级（等级只影响强化次数标记）。"""
    low = copy.deepcopy(make_ganyu_panel()["圣遗物"][0])
    high = copy.deepcopy(low)
    low["等级"] = 0
    high["等级"] = 20

    effective, _ = au.get_effective(make_ganyu_panel())
    _, point_mark, max_mark = au.get_miao_score(
        copy.deepcopy(effective), copy.deepcopy(role_info_json["甘雨"]["属性"])
    )
    grade_low, score_low, _ = au.get_artifact_score(point_mark, max_mark, low, "冰", 0, "甘雨")
    grade_high, score_high, _ = au.get_artifact_score(point_mark, max_mark, high, "冰", 0, "甘雨")
    assert (grade_low, score_low) == (grade_high, score_high)


def test_artifact_score_wrong_element_mainstat_is_penalized():
    """元素不符的主词条（冰角色看火伤杯）会失去主词条分。"""
    data = make_ganyu_panel()
    matched = copy.deepcopy(data["圣遗物"][3])
    mismatched = copy.deepcopy(matched)
    mismatched["主属性"] = {"属性名": "火元素伤害加成", "属性值": 46.6}

    effective, _ = au.get_effective(data)
    _, point_mark, max_mark = au.get_miao_score(
        copy.deepcopy(effective), copy.deepcopy(role_info_json["甘雨"]["属性"])
    )
    _, good_score, _ = au.get_artifact_score(point_mark, max_mark, matched, "冰", 3, "甘雨")
    _, bad_score, _ = au.get_artifact_score(point_mark, max_mark, mismatched, "冰", 3, "甘雨")
    assert bad_score < good_score


def test_artifact_score_energy_recharge_mainstat_exempt_from_penalty():
    """元素充能效率主词条豁免主词条惩罚。"""
    data = make_ganyu_panel()
    artifact = copy.deepcopy(data["圣遗物"][2])
    artifact["主属性"] = {"属性名": "元素充能效率", "属性值": 51.8}
    effective, _ = au.get_effective(data)
    _, point_mark, max_mark = au.get_miao_score(
        copy.deepcopy(effective), copy.deepcopy(role_info_json["甘雨"]["属性"])
    )
    grade, score, _ = au.get_artifact_score(point_mark, max_mark, artifact, "冰", 2, "甘雨")
    assert score > 0
    assert grade in {"D", "C", "B", "A", "S", "SS", "SSS", "ACE", "ACE*"}


def test_artifact_score_empty_weights_raises():
    """空权重会触发异常（记录当前行为：KeyError，而非静默返回 0）。

    源实现的这一路径依赖上游数据完整性；当前 score.json 中没有全 0 权重的
    角色，因此正常流程不会触达。这里把行为固化下来便于日后回归。
    """
    artifact = make_ganyu_panel()["圣遗物"][0]
    with pytest.raises((KeyError, ZeroDivisionError)):
        au.get_artifact_score({}, {}, artifact, "冰", 0, "甘雨")


# ---------------------------------------------------------------------------
# 数据文件一致性
# ---------------------------------------------------------------------------
def test_score_json_covers_rules():
    """score.json 中的每个角色必须有完整的 10 项权重键。"""
    from nonebot_plugin_genshin_role_info.utils.card_utils import role_ori

    expected_keys = {
        "hp",
        "atk",
        "def",
        "cpct",
        "cdmg",
        "mastery",
        "dmg",
        "phy",
        "recharge",
        "heal",
    }
    assert len(role_ori) >= 120
    for name, weights in role_ori.items():
        assert set(weights) == expected_keys, f"{name} 的权重键不完整"


def test_no_all_zero_weight_role():
    """不应存在权重全 0 的角色（会导致评分为 0/异常）。"""
    from nonebot_plugin_genshin_role_info.utils.card_utils import role_ori

    zero_roles = [
        name for name, weights in role_ori.items() if not any(weights.values())
    ]
    assert not zero_roles, f"以下角色权重全为 0: {zero_roles}"


def test_artifact_json_structure():
    """artifact.json 的 Name/Mapping/Piece/Info 四段必须齐备。"""
    from nonebot_plugin_genshin_role_info.utils.card_utils import artifact_list

    for key in ("Name", "Mapping", "Piece", "Info"):
        assert key in artifact_list, f"artifact.json 缺少 {key}"
    assert len(artifact_list["Name"]) > 100
    assert len(artifact_list["Mapping"]) > 50
    # Piece 的 5 个部位
    piece_names = {value[1] for value in artifact_list["Piece"].values()}
    assert piece_names == {"生之花", "死之羽", "时之沙", "空之杯", "理之冠"}
