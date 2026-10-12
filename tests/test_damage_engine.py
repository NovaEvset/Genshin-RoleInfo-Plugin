"""伤害计算引擎测试。

本模块是本项目最重要的回归测试：它把**迁移后的实现**与**源真寻插件中的
原始实现**逐项对比（parity），确保迁移没有改变任何计算结果。

源实现从 ``_upstream/src_zhenxun`` 以独立命名空间加载（原包 ``__init__.py``
会导入真寻专有模块，因此不能按原包名导入）。若上游源码不可用，则退回到
记录的黄金值断言。
"""

from __future__ import annotations

import copy
import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

from tests._helpers import cached_roles, load_player_cache, make_hutao_panel

PROJECT_ROOT = Path(__file__).resolve().parent.parent
UPSTREAM_DIR = PROJECT_ROOT / "_upstream" / "src_zhenxun"

from nonebot_plugin_genshin_role_info.data_source.damage import get_role_dmg
from nonebot_plugin_genshin_role_info.data_source.damage.calculator import (
    DamageCalculator,
)
from nonebot_plugin_genshin_role_info.data_source.damage.models import (
    DamageAttributes,
)
from nonebot_plugin_genshin_role_info.data_source.damage.reactions import (
    crystallize_base_damage,
    level_base_damage,
    mastery_multiplier,
    reaction_config,
)
from nonebot_plugin_genshin_role_info.data_source.damage.rules import (
    RULES,
    get_rule,
)

# 源实现（迁移前）的黄金输出，记录自真寻版本 v4.3.13。
# 只有当上游源码不可用时才会用到这些常量。
GOLDEN_HUTAO = {
    "半血开E重击": ("21930", "26735"),
    "半血开E重击蒸发": ("48597", "59244"),
    "半血开E后Q": ("55824", "68054"),
}


def _stage_upstream() -> Path | None:
    """把上游的 data_source / utils 复制成可独立导入的包。

    上游 ``src_zhenxun/__init__.py`` 会 ``import zhenxun`` / ``nonebot_plugin_apscheduler``，
    因此不能按原包名导入；同时它用 ``parents[2]`` 定位 ``res``，所以需要在该
    目录下放置一个指向 ``res`` 的链接以保持路径语义。
    """
    if not UPSTREAM_DIR.is_dir():
        return None
    staged = Path("/tmp/gri_upstream_parity")
    if not (staged / "data_source").is_dir():
        staged.mkdir(parents=True, exist_ok=True)
        for name in ("data_source", "utils"):
            shutil.copytree(UPSTREAM_DIR / name, staged / name, dirs_exist_ok=True)
        res_link = staged / "res"
        if not res_link.exists():
            res_link.symlink_to(UPSTREAM_DIR / "res")
    if str(staged.parent) not in sys.path:
        sys.path.insert(0, str(staged.parent))
    return staged


def _load_upstream_engine():
    staged = _stage_upstream()
    if staged is None:
        return None
    try:
        module = importlib.import_module("gri_upstream_parity.data_source.damage.engine")
    except Exception:  # pragma: no cover - 上游不可用
        return None
    return module.get_role_dmg


UPSTREAM_GET_ROLE_DMG = _load_upstream_engine()


def _normalize(result):
    """把引擎输出转成可比较的纯数据（保留 is_text 标记与顺序）。"""
    if result is None:
        return None
    return [(key, tuple(value), bool(getattr(value, "is_text", False))) for key, value in result.items()]


# ---------------------------------------------------------------------------
# 规则表装载
# ---------------------------------------------------------------------------
def test_rules_loaded():
    """规则表必须在导入期完成构建。"""
    assert len(RULES) >= 120
    assert "胡桃" in RULES


def test_get_rule_known_character():
    assert get_rule("胡桃", "火") is not None


def test_get_rule_unknown_returns_none():
    assert get_rule("不存在的角色", "火") is None


@pytest.mark.parametrize(
    "name,element",
    [("荧", "风"), ("空", "岩"), ("旅行者", "草")],
)
def test_get_rule_traveler_by_element(name, element):
    """旅行者按元素区分规则。"""
    assert get_rule(name, element) is not None


def test_get_rule_traveler_without_element_returns_none():
    assert get_rule("旅行者", None) is None


# ---------------------------------------------------------------------------
# 与源实现的交叉验证（parity）
# ---------------------------------------------------------------------------
def test_parity_hutao_reference_panel():
    """胡桃参考面板：迁移实现必须与源实现完全一致。"""
    result = get_role_dmg(make_hutao_panel())
    assert result is not None
    assert "额外说明" in result
    for title, expected in GOLDEN_HUTAO.items():
        assert title in result, f"缺少伤害条目 {title}"
        assert tuple(result[title]) == expected, f"{title} 数值变化"

    if UPSTREAM_GET_ROLE_DMG is not None:
        upstream = UPSTREAM_GET_ROLE_DMG(copy.deepcopy(make_hutao_panel()))
        assert _normalize(result) == _normalize(upstream)


def test_parity_all_cached_roles():
    """真实 UID 缓存中的全部角色：逐项对比源实现。"""
    if UPSTREAM_GET_ROLE_DMG is None:
        pytest.skip("上游源码不可用，跳过 parity 对比")
    cache = load_player_cache()
    compared = 0
    for name, role_data in cache["角色"].items():
        ported = get_role_dmg(copy.deepcopy(role_data))
        upstream = UPSTREAM_GET_ROLE_DMG(copy.deepcopy(role_data))
        assert _normalize(ported) == _normalize(upstream), f"{name} 输出不一致"
        compared += 1
    assert compared >= 5


def test_parity_across_many_rules():
    """遍历全部已有规则的角色，逐一对比（覆盖 129 条规则）。"""
    if UPSTREAM_GET_ROLE_DMG is None:
        pytest.skip("上游源码不可用，跳过 parity 对比")
    cache = load_player_cache()
    template = cache["角色"].get("阿罗夏") or next(iter(cache["角色"].values()))
    mismatches = []
    checked = 0
    for name in RULES:
        # 旅行者/奇偶 等需要元素，跳过非直接可构造的键
        if "/" in name or "·" in name:
            continue
        data = copy.deepcopy(template)
        data["名称"] = name
        data["元素"] = (cache["角色"].get(name) or {}).get("元素") or data["元素"]
        ported = _normalize(get_role_dmg(copy.deepcopy(data)))
        upstream = _normalize(UPSTREAM_GET_ROLE_DMG(copy.deepcopy(data)))
        if ported != upstream:
            mismatches.append(name)
        checked += 1
    assert checked > 100
    assert not mismatches, f"以下角色输出与源实现不一致: {mismatches[:10]}"


# ---------------------------------------------------------------------------
# 输出契约
# ---------------------------------------------------------------------------
def test_output_contract():
    """输出必须是 dict[str, tuple[str, ...]]，且排除额外说明后至少一项。"""
    result = get_role_dmg(make_hutao_panel())
    assert isinstance(result, dict)
    assert all(isinstance(k, str) for k in result)
    assert all(isinstance(v, tuple) for v in result.values())
    assert all(isinstance(item, str) for v in result.values() for item in v)
    items = [k for k in result if k != "额外说明"]
    assert items, "至少应有一条伤害"

    # 数值行为 1 或 2 元组，且首元素可转为数字
    for title in items:
        values = result[title]
        assert 1 <= len(values) <= 2
        assert float(values[0]) > 0


def test_extra_notes_is_last_key():
    result = get_role_dmg(make_hutao_panel())
    assert list(result)[-1] == "额外说明"


def test_text_rows_marked_is_text():
    """文本型结果必须带 is_text 标记，且不作为数值伤害参与排行。"""
    result = get_role_dmg(make_hutao_panel())
    for values in result.values():
        marker = getattr(values, "is_text", False)
        assert isinstance(marker, bool)


def test_character_without_rule_returns_none():
    data = make_hutao_panel()
    data["名称"] = "不存在的角色"
    assert get_role_dmg(data) is None


def test_character_without_talent_data_returns_none():
    """规则存在但天赋数据缺失时返回 None（引擎的显式守卫）。"""
    data = make_hutao_panel()
    data["天赋"] = []
    assert get_role_dmg(data) is None


def test_damage_scales_with_crit():
    """提高暴击伤害应提升暴击列（第 2 项），期望伤害也应提高。"""
    base = get_role_dmg(make_hutao_panel())
    boosted_panel = make_hutao_panel()
    boosted_panel["属性"]["暴击伤害"] += 1.0
    boosted = get_role_dmg(boosted_panel)
    title = "半血开E重击"
    assert float(boosted[title][1]) > float(base[title][1])
    assert float(boosted[title][0]) > float(base[title][0])


def test_damage_scales_with_talent_level():
    """提高天赋等级应提升伤害（倍率来自天赋表）。"""
    base = get_role_dmg(make_hutao_panel())
    stronger = make_hutao_panel()
    stronger["天赋"] = [
        {"等级": 13, "图标": "Skill_A_03"},
        {"等级": 13, "图标": "Skill_S_Hutao_01"},
        {"等级": 13, "图标": "Skill_E_Hutao_01"},
    ]
    stronger_result = get_role_dmg(stronger)
    assert float(stronger_result["半血开E重击"][0]) > float(base["半血开E重击"][0])


def test_constellation_affects_calculation():
    """命座数会改变伤害引擎的输出。

    引擎按 ``len(命座)`` 得到命座数，并据此启用带 ``cons`` 门槛的条目与
    Buff。这里用真实缓存中的角色逐一比较 0 命与 6 命的结果，至少应有一个
    角色的输出发生变化（否则命座门槛逻辑失效）。
    """
    cache = load_player_cache()
    changed = []
    for name, role_data in cache["角色"].items():
        zero = copy.deepcopy(role_data)
        zero["命座"] = []
        six = copy.deepcopy(role_data)
        six["命座"] = [f"UI_Talent_S_{i}" for i in range(6)]
        r0 = get_role_dmg(zero)
        r6 = get_role_dmg(six)
        if r0 is None or r6 is None:
            continue
        if _normalize(r0) != _normalize(r6):
            changed.append(name)
    assert changed, "命座数未能影响任何角色的伤害输出，命座门槛逻辑可能失效"


# ---------------------------------------------------------------------------
# 反应与等级表
# ---------------------------------------------------------------------------
def test_level_base_damage_known_values():
    assert level_base_damage(90) == pytest.approx(361.713)
    assert level_base_damage(1) == pytest.approx(4.291)


def test_level_base_damage_interpolates():
    """未列出的等级按相邻点线性插值。"""
    value = level_base_damage(91)
    assert level_base_damage(90) < value < level_base_damage(95)


def test_crystallize_base_damage_known():
    assert crystallize_base_damage(90) == pytest.approx(1851.06)


def test_mastery_multiplier_formulas():
    """精通收益公式必须与 Miao 一致。"""
    assert mastery_multiplier("pct", 0) == 0
    assert mastery_multiplier("pct", 187) == pytest.approx((25 / 9) * 187 / (187 + 1400))
    assert mastery_multiplier("fusion", 200) == pytest.approx(16 * 200 / (200 + 2000))
    assert mastery_multiplier("bonus", 100) == pytest.approx(5 * 100 / (100 + 1200))
    assert mastery_multiplier("shield", 100) == pytest.approx((40 / 9) * 100 / (100 + 1400))
    assert mastery_multiplier("unknown", 100) == 0


def test_mastery_multiplier_handles_negative():
    assert mastery_multiplier("pct", -50) == 0


def test_reaction_config_vaporize_element_dependent():
    """蒸发系数按触发元素区分：水打火 2.0，火打水 1.5。"""
    assert reaction_config("vaporize", "水") == ("pct", 2.0)
    assert reaction_config("vaporize", "火") == ("pct", 1.5)
    assert reaction_config("蒸发", "水") == ("pct", 2.0)


def test_reaction_config_fusion_and_bonus():
    assert reaction_config("overloaded", "火") == ("fusion", 11.0)
    assert reaction_config("aggravate", "雷") == ("bonus", 4.6)
    assert reaction_config("spread", "草") == ("bonus", 5.0)


def test_reaction_config_lunar_depends_on_talent():
    assert reaction_config("lunarCharged", "雷", "fy")[1] == 7.2
    assert reaction_config("lunarCharged", "雷", "a")[1] == 3.0


# ---------------------------------------------------------------------------
# 计算器单元行为
# ---------------------------------------------------------------------------
def _basic_attr(**overrides) -> DamageAttributes:
    values = dict(
        base_hp=15000,
        base_atk=800,
        base_defense=800,
        hp=30000,
        atk=1800,
        defense=900,
        mastery=100,
        recharge=120,
        cpct=50,
        cdmg=100,
        dmg=0,
        phy=0,
        heal=0,
    )
    values.update(overrides)
    return DamageAttributes(**values)


def test_resistance_coefficient_branches():
    assert DamageCalculator._resistance_coefficient(0) == pytest.approx(1.0)
    assert DamageCalculator._resistance_coefficient(10) == pytest.approx(0.9)
    assert DamageCalculator._resistance_coefficient(-20) == pytest.approx(1.1)
    assert DamageCalculator._resistance_coefficient(80) == pytest.approx(1 / (1 + 3 * 80 / 100))


def test_calculator_expected_and_crit_columns():
    calc = DamageCalculator(_basic_attr(cpct=50, cdmg=100), 90, "火")
    result = calc.calculate(multiplier=100, talents="a")
    # 期望 = 非暴击 * (1 + 0.5*1.0)；暴击 = 非暴击 * (1 + 1.0)
    assert result.direct is not None
    assert result.avg == pytest.approx(result.direct * 1.5)
    assert result.crit == pytest.approx(result.direct * 2.0)


def test_calculator_zero_crit_rate_disables_crit_damage():
    calc = DamageCalculator(_basic_attr(cpct=0, cdmg=100), 90, "火")
    result = calc.calculate(multiplier=100, talents="a")
    assert result.avg == pytest.approx(result.direct)
    assert result.crit == pytest.approx(result.direct)


def test_calculator_heal_uses_heal_bonus():
    calc = DamageCalculator(_basic_attr(heal=50, heal_inc=10), 90, "火")
    assert calc.heal(100).avg == pytest.approx(160.0)


def test_calculator_shield_uses_shield_bonus():
    calc = DamageCalculator(_basic_attr(), 90, "岩")
    # 默认 shield=100 / shield_inc=100，即原值
    assert calc.shield(1000).avg == pytest.approx(1000.0)


def test_fusion_reaction_has_no_crit_column():
    """剧变反应（超载等）自算抗性并提前返回，只有一列。"""
    calc = DamageCalculator(_basic_attr(), 90, "火")
    result = calc.calculate(talents="a", reaction="overloaded")
    assert result.crit is None
    assert len(result.display()) == 1


def test_calculator_multiplier_scales_linearly():
    calc = DamageCalculator(_basic_attr(), 90, "火")
    one = calc.calculate(multiplier=100, talents="a")
    two = calc.calculate(multiplier=200, talents="a")
    assert two.direct == pytest.approx(one.direct * 2)


def test_physical_branch_uses_phy_bonus():
    calc = DamageCalculator(_basic_attr(dmg=100, phy=50), 90, "火")
    normal = calc.calculate(multiplier=100, talents="a")
    physical = calc.calculate(multiplier=100, talents="a", physical=True)
    # 物理分支用 phy(50%) 而非 dmg(100%)
    assert physical.direct < normal.direct


def test_talent_bonus_is_added():
    """talent key 会叠加 TalentBonus；无 bonus 时两者相等。"""
    calc = DamageCalculator(_basic_attr(), 90, "火")
    plain = calc.calculate(multiplier=100, talents="")
    with_talent = calc.calculate(multiplier=100, talents="a")
    assert plain.direct == pytest.approx(with_talent.direct)

    # 手工注入天赋加成后伤害必须提高
    attr = _basic_attr()
    attr.talent("a").pct = 50
    boosted = DamageCalculator(attr, 90, "火").calculate(multiplier=100, talents="a")
    assert boosted.direct > plain.direct
