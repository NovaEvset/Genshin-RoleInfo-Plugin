"""排行聚合逻辑测试。

覆盖 ``utils/rank_utils.py``：伤害项目选取、群成员排行聚合、排行条目构造。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nonebot_plugin_genshin_role_info.data_source.damage.models import DamageValues
from nonebot_plugin_genshin_role_info.utils.rank_utils import (
    build_rank_entry,
    collect_damage_titles,
    collect_role_rank_entries,
    select_damage_metric,
)

from tests._helpers import load_player_cache


# ---------------------------------------------------------------------------
# select_damage_metric
# ---------------------------------------------------------------------------
def test_select_damage_metric_one_based_index():
    damage = {"条目A": ("100", "200"), "条目B": ("300", "400")}
    assert select_damage_metric(damage, 1) == ("条目A", 100.0, 200.0)
    assert select_damage_metric(damage, 2) == ("条目B", 300.0, 400.0)


def test_select_damage_metric_excludes_extra_notes():
    """“额外说明”不参与序号计算。"""
    damage = {"条目A": ("100", "200"), "额外说明": ("说明文本",)}
    assert select_damage_metric(damage, 1)[0] == "条目A"
    # 序号 2 越界（因为额外说明被排除）
    assert select_damage_metric(damage, 2) is None


def test_select_damage_metric_out_of_range():
    damage = {"条目A": ("100", "200")}
    assert select_damage_metric(damage, 0) is None
    assert select_damage_metric(damage, 5) is None
    assert select_damage_metric(None, 1) is None
    assert select_damage_metric({}, 1) is None


def test_select_damage_metric_skips_text_rows():
    """Miao 的 type:text 行不是数值伤害。"""
    damage = {
        "文本行": DamageValues(("34.0%",), is_text=True),
        "数值行": ("500", "700"),
    }
    # 文本行不可选
    assert select_damage_metric(damage, 1) is None
    assert select_damage_metric(damage, 2) == ("数值行", 500.0, 700.0)


def test_select_damage_metric_single_value_row():
    """单列（如护盾量）没有暴击列，第三项为 None。"""
    assert select_damage_metric({"护盾": ("5288",)}, 1) == ("护盾", 5288.0, None)


def test_select_damage_metric_parses_percent_and_commas():
    assert select_damage_metric({"条目": ("1,234%",)}, 1) == ("条目", 1234.0, None)


# ---------------------------------------------------------------------------
# build_rank_entry
# ---------------------------------------------------------------------------
def _role_data(**overrides):
    cache = load_player_cache()
    data = json.loads(json.dumps(cache["角色"]["阿罗夏"], ensure_ascii=False))
    data["评分"] = 100.0
    data.update(overrides)
    return data


def test_build_rank_entry_score_metric():
    entry = build_rank_entry(10001, "测试用户", _role_data(), "评分")
    assert entry is not None
    assert entry["qq"] == 10001
    assert entry["nickname"] == "测试用户"
    assert entry["value"] == 100.0
    assert entry["damage_title"] is None
    # 展示行固定为 5 项
    assert [row[0] for row in entry["rows"]] == [
        "生命值",
        "暴击率",
        "暴击伤害",
        "元素精通",
        "元素充能效率",
    ]
    assert entry["rank"] == len(_role_data()["命座"])
    assert "星级" in entry["weapon"]


def test_build_rank_entry_score_metric_without_score():
    data = _role_data()
    data.pop("评分", None)
    assert build_rank_entry(1, "x", data, "评分") is None


def test_build_rank_entry_damage_metric():
    damage = {"条目A": ("1000", "2000"), "额外说明": ("note",)}
    entry = build_rank_entry(1, "x", _role_data(), "伤害", damage, 1)
    assert entry is not None
    assert entry["value"] == 1000.0
    assert entry["damage_title"] == "条目A"


def test_build_rank_entry_damage_requires_index():
    assert build_rank_entry(1, "x", _role_data(), "伤害", {"a": ("1",)}, None) is None


def test_build_rank_entry_damage_out_of_range():
    assert build_rank_entry(1, "x", _role_data(), "伤害", {"a": ("1",)}, 9) is None


def test_build_rank_entry_missing_properties():
    data = _role_data()
    data.pop("属性", None)
    assert build_rank_entry(1, "x", data, "评分") is None


def test_build_rank_entry_rejects_unknown_metric():
    with pytest.raises(ValueError):
        build_rank_entry(1, "x", _role_data(), "未知")


def test_build_rank_entry_formats_rows():
    entry = build_rank_entry(1, "x", _role_data(), "评分")
    rows = dict(entry["rows"])
    # 暴击率/暴击伤害/充能以百分比展示
    assert rows["暴击率"].endswith("%")
    assert rows["暴击伤害"].endswith("%")
    assert rows["元素充能效率"].endswith("%")
    # 生命值为整数合计（含千分位）
    assert "," in rows["生命值"]


# ---------------------------------------------------------------------------
# collect_role_rank_entries
# ---------------------------------------------------------------------------
def _write_cache(directory: Path, uid: str, name: str, role_data: dict, score: float):
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "玩家信息": {"昵称": name, "更新时间": "2026-01-01 00:00:00"},
        "角色": {role_data["名称"]: {**role_data, "评分": score}},
        "圣遗物榜单": [],
        "大毕业圣遗物": 0,
        "小毕业圣遗物": 0,
        "圣遗物列表": [[], [], [], [], []],
    }
    (directory / f"{uid}.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def test_collect_role_rank_entries_sorts_and_limits(tmp_path: Path):
    role_name = "阿罗夏"
    members = []
    uid_map = {}
    for index in range(20):
        qq = 20000 + index
        uid = f"10000000{index:02d}"
        members.append({"user_id": qq, "nickname": f"用户{index}", "card": ""})
        uid_map[str(qq)] = uid
        _write_cache(tmp_path, uid, role_name, _role_data(), float(index))

    entries = collect_role_rank_entries(
        members, uid_map, tmp_path, role_name, "评分", limit=16
    )
    assert len(entries) == 16
    values = [entry["value"] for entry in entries]
    assert values == sorted(values, reverse=True)
    assert values[0] == 19.0


def test_collect_role_rank_entries_skips_unbound(tmp_path: Path):
    members = [
        {"user_id": 1, "nickname": "未绑定", "card": ""},
        {"user_id": 2, "nickname": "已绑定", "card": "群名片"},
    ]
    _write_cache(tmp_path, "100000001", "阿罗夏", _role_data(), 50.0)
    entries = collect_role_rank_entries(
        members, {"2": "100000001"}, tmp_path, "阿罗夏", "评分"
    )
    assert len(entries) == 1
    assert entries[0]["qq"] == 2
    # 群名片优先于昵称
    assert entries[0]["nickname"] == "群名片"


def test_collect_role_rank_entries_respects_ranking_order(tmp_path: Path):
    """同分时按 QQ 升序，保证结果稳定可复现。"""
    members = [
        {"user_id": 9, "nickname": "九", "card": ""},
        {"user_id": 3, "nickname": "三", "card": ""},
    ]
    _write_cache(tmp_path, "100000009", "阿罗夏", _role_data(), 10.0)
    _write_cache(tmp_path, "100000003", "阿罗夏", _role_data(), 10.0)
    entries = collect_role_rank_entries(
        members, {"9": "100000009", "3": "100000003"}, tmp_path, "阿罗夏", "评分"
    )
    assert [entry["qq"] for entry in entries] == [3, 9]


def test_collect_role_rank_entries_damage_metric(tmp_path: Path):
    role_name = "阿罗夏"
    _write_cache(tmp_path, "100000001", role_name, _role_data(), 0.0)
    members = [{"user_id": 1, "nickname": "n", "card": ""}]

    def fake_calculator(role_data):
        return {"条目A": ("500", "700"), "额外说明": ("note",)}

    entries = collect_role_rank_entries(
        members,
        {"1": "100000001"},
        tmp_path,
        role_name,
        "伤害",
        damage_calculator=fake_calculator,
        damage_index=1,
    )
    assert len(entries) == 1
    assert entries[0]["value"] == 500.0
    assert entries[0]["damage_title"] == "条目A"


def test_collect_role_rank_entries_ignores_broken_files(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "100000001.json").write_text("{ broken", encoding="utf-8")
    members = [{"user_id": 1, "nickname": "n", "card": ""}]
    assert collect_role_rank_entries(
        members, {"1": "100000001"}, tmp_path, "阿罗夏", "评分"
    ) == []


def test_collect_role_rank_entries_ignores_missing_role(tmp_path: Path):
    _write_cache(tmp_path, "100000001", "阿罗夏", _role_data(), 10.0)
    members = [{"user_id": 1, "nickname": "n", "card": ""}]
    assert collect_role_rank_entries(
        members, {"1": "100000001"}, tmp_path, "胡桃", "评分"
    ) == []


def test_collect_role_rank_entries_handles_bad_user_id(tmp_path: Path):
    members = [{"user_id": "not-a-number", "nickname": "n", "card": ""}]
    assert collect_role_rank_entries(members, {}, tmp_path, "阿罗夏", "评分") == []


# ---------------------------------------------------------------------------
# collect_damage_titles
# ---------------------------------------------------------------------------
def test_collect_damage_titles_returns_first_group_member_titles(tmp_path: Path):
    _write_cache(tmp_path, "100000001", "阿罗夏", _role_data(), 0.0)
    members = [{"user_id": 1}, {"user_id": 2}]

    def calculator(role_data):
        return {"条目A": ("1",), "条目B": ("2",), "额外说明": ("n",)}

    titles = collect_damage_titles(members, {"1": "100000001"}, tmp_path, "阿罗夏", calculator)
    assert titles == ["条目A", "条目B"]


def test_collect_damage_titles_skips_members_without_cache(tmp_path: Path):
    members = [{"user_id": 1}]
    assert collect_damage_titles(members, {"1": "999999999"}, tmp_path, "阿罗夏", lambda d: {}) == []


def test_collect_damage_titles_tolerates_calculator_errors(tmp_path: Path):
    _write_cache(tmp_path, "100000001", "阿罗夏", _role_data(), 0.0)
    members = [{"user_id": 1}]

    def broken(role_data):
        raise RuntimeError("boom")

    assert collect_damage_titles(members, {"1": "100000001"}, tmp_path, "阿罗夏", broken) == []


def test_collect_damage_titles_ignores_non_mapping_uid_map(tmp_path: Path):
    assert collect_damage_titles([{"user_id": 1}], None, tmp_path, "阿罗夏", lambda d: {}) == []
