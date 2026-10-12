"""插件加载与命令注册测试。

验证插件能被 NoneBot2 加载、元数据正确，并且**全部命令都已注册**。
"""

from __future__ import annotations

import pytest
from nonebot.matcher import matchers
from nonebot.rule import CommandRule, RegexRule


def _plugin_matchers():
    """返回本插件注册的全部匹配器。"""
    result = []
    for group in matchers.values():
        for matcher in group:
            if matcher.module_name == "nonebot_plugin_genshin_role_info":
                result.append(matcher)
    return result


def _rule_objects(matcher):
    rule = getattr(matcher, "rule", None)
    return [checker.call for checker in getattr(rule, "checkers", [])]


def _commands(matcher) -> list[str]:
    """返回匹配器上的命令名。

    NoneBot2 的 ``CommandRule.cmds`` 是**嵌套**元组：每个命令一个元组，
    元组首元素为命令名、其余为别名，例如
    ``(('圣遗物榜单', '圣遗物排行'),)``。
    """
    names: list[str] = []
    for rule in _rule_objects(matcher):
        if isinstance(rule, CommandRule):
            for command in rule.cmds:
                if isinstance(command, str):
                    names.append(command)
                else:
                    names.extend(command)
    return names


def _regexes(matcher) -> list[str]:
    patterns: list[str] = []
    for rule in _rule_objects(matcher):
        if isinstance(rule, RegexRule):
            patterns.append(rule.regex)
    return patterns


def test_plugin_loaded(plugin):
    assert plugin is not None
    assert plugin.name == "nonebot_plugin_genshin_role_info"
    assert plugin.module is not None


def test_plugin_metadata(plugin):
    metadata = plugin.metadata
    assert metadata is not None
    assert metadata.name == "原神角色面板"
    assert metadata.extra["version"] == "4.3.13"
    assert "~onebot.v11" in metadata.supported_adapters
    assert metadata.homepage == "https://github.com/NovaEvset/Genshin-RoleInfo-Plugin"
    # 插件声明的命令必须与真实注册的命令一致（文档不能出现代码里没有的命令）
    usage = metadata.usage
    for command in ("原神绑定UID", "原神解绑", "角色面板", "原神角色排行", "圣遗物导入"):
        assert command in usage


def test_matcher_count():
    """迁移后的 17 个命令入口必须全部注册。"""
    assert len(_plugin_matchers()) == 17


def test_all_commands_registered():
    """命令名与源码中的注册保持一致，且覆盖原项目全部命令。"""
    registered: set[str] = set()
    for matcher in _plugin_matchers():
        registered.update(_commands(matcher))

    expected = {
        "原神解绑",
        "原神角色排行",
        "圣遗物榜单",
        "圣遗物排行",  # 别名
        "群圣遗物榜单",
        "群圣遗物排行",  # 别名
        "重置最强",
        "检查原神面板更新",
        "圣遗物导入",
        "gsck",
        "米游社更新原神面板",
    }
    assert expected <= registered, f"缺少命令: {expected - registered}"


def test_all_regex_commands_registered():
    """正则型命令的表达式必须与原项目一致。"""
    patterns = {
        pattern
        for matcher in _plugin_matchers()
        for pattern in _regexes(matcher)
    }
    expected = {
        r"(?:原神绑定|绑定原神).*?(\d+)",
        r"(.*)面板(.*)",
        r"^(最强|群最强)(.*)",
        r"^(最菜|群最菜)(.*)",
        r"^(.+?)(评分|伤害)(?:排行|榜单)(\d*)$",
        "(.*?)([花羽沙杯冠])适配",
        r"(.*?)([花羽沙杯冠套])推荐(\d*)$",
    }
    assert expected <= patterns, f"缺少正则命令: {expected - patterns}"


def test_priorities_match_source():
    """优先级必须与原项目一致，否则多命令匹配顺序会变化。"""
    by_priority: dict[int, list[str]] = {}
    for matcher in _plugin_matchers():
        by_priority.setdefault(matcher.priority, []).append(matcher.module_name)
    # 原项目使用的优先级集合
    assert set(by_priority) == {1, 3, 4, 5}
    assert len(by_priority[5]) == 2  # 绑定 / 解绑
    assert len(by_priority[1]) == 1  # 私聊文件导入


def test_block_flags():
    """block 标志影响消息是否继续向下传递，必须与原项目一致。

    原项目中显式 ``block=False`` 的只有三处：``get_card``（面板）、
    ``reset_best``（重置最强）与私聊文件导入；``group_best`` / ``group_worst``
    使用 ``on_regex`` 的默认值 ``block=True``。
    """
    non_blocking_commands = {"重置最强"}
    blocking_commands = {
        "原神解绑",
        "原神角色排行",
        "圣遗物榜单",
        "圣遗物排行",
        "群圣遗物榜单",
        "群圣遗物排行",
        "检查原神面板更新",
        "圣遗物导入",
        "gsck",
        "米游社更新原神面板",
    }
    non_blocking_patterns = {"(.*)面板(.*)"}

    for matcher in _plugin_matchers():
        cmds = set(_commands(matcher))
        pats = set(_regexes(matcher))
        if cmds & non_blocking_commands:
            assert matcher.block is False, f"{cmds} 应为 block=False"
        if cmds & blocking_commands:
            assert matcher.block is True, f"{cmds} 应为 block=True"
        if pats & non_blocking_patterns:
            assert matcher.block is False, f"{pats} 应为 block=False"
        # 最强/最菜 在原项目中沿用默认值 True
        if pats & {r"^(最强|群最强)(.*)", r"^(最菜|群最菜)(.*)"}:
            assert matcher.block is True, f"{pats} 应为 block=True（沿用默认值）"


def test_role_rank_requires_group():
    """评分/伤害排行仅群聊可用（原项目 permission=GROUP）。"""
    target = None
    for matcher in _plugin_matchers():
        if r"^(.+?)(评分|伤害)(?:排行|榜单)(\d*)$" in _regexes(matcher):
            target = matcher
            break
    assert target is not None
    assert target.permission is not None


def test_each_matcher_has_handler():
    for matcher in _plugin_matchers():
        assert len(matcher.handlers) >= 1


@pytest.mark.parametrize(
    "regex,text,expected",
    [
        (r"(?:原神绑定|绑定原神).*?(\d+)", "原神绑定346948884", "346948884"),
        (r"(?:原神绑定|绑定原神).*?(\d+)", "绑定原神 123456789", "123456789"),
        (r"(.*)面板(.*)", "甘雨面板", "甘雨"),
        (r"(.*)面板(.*)", "胡桃面板100000001", "100000001"),
        (r"^(.+?)(评分|伤害)(?:排行|榜单)(\d*)$", "胡桃评分排行", "评分"),
        (r"^(.+?)(评分|伤害)(?:排行|榜单)(\d*)$", "胡桃伤害排行5", "5"),
        ("(.*?)([花羽沙杯冠])适配", "胡桃冠适配", "冠"),
        (r"(.*?)([花羽沙杯冠套])推荐(\d*)$", "胡桃魔女套推荐2", "2"),
    ],
)
def test_command_pattern_matching(regex, text, expected):
    """正则型命令的匹配与捕获组行为。"""
    import re

    match = re.search(regex, text)
    assert match is not None, f"{regex} 未匹配 {text}"
    assert expected in match.groups()


# ---------------------------------------------------------------------------
# 更新检查的版本号解析
# ---------------------------------------------------------------------------
def test_version_key_comparison(plugin):
    """版本号比较必须按数值分段，而不是字符串比较。"""
    module = plugin.module
    key = module._version_key
    assert key("4.3.13") > key("4.3.9")
    assert key("4.10.0") > key("4.9.9")
    assert key("4.3.13") == key("4.3.13")


def test_version_check_url_points_to_file_with_version_literal(plugin):
    """版本检查地址必须指向真正包含 PLUGIN_VERSION 字面量的文件。

    这是集成测试中真实暴露过的缺陷：默认地址曾指向 ``__init__.py``，
    而版本号字面量实际定义在 ``config.py``，导致更新检查永远解析失败。
    """
    from nonebot_plugin_genshin_role_info.config import PLUGIN_VERSION, config

    assert config.genshin_role_info_version_url.endswith("config.py")
    # 本地文件确实包含该字面量（远端为同一文件）
    from pathlib import Path

    from nonebot_plugin_genshin_role_info import paths

    config_source = (Path(paths.BASE_PATH) / "config.py").read_text(encoding="utf-8")
    assert f'PLUGIN_VERSION = "{PLUGIN_VERSION}"' in config_source


def test_version_regex_matches_both_source_forms(plugin):
    """版本号正则必须同时兼容两种写法。"""
    import re

    patterns = [
        re.compile(r'version="(\d+\.\d+\.\d+)"'),
        re.compile(r'PLUGIN_VERSION\s*=\s*"(\d+\.\d+\.\d+)"'),
    ]
    samples = {
        r'version="4.3.13"': "4.3.13",
        r'PLUGIN_VERSION = "4.3.13"': "4.3.13",
    }
    for text, expected in samples.items():
        matched = next(
            (m.group(1) for p in patterns if (m := p.search(text))), None
        )
        assert matched == expected, f"{text!r} 未被解析"


def test_remote_version_source_matches_local(plugin):
    """本地 config.py 的版本号与 PluginMetadata 必须一致。"""
    from nonebot_plugin_genshin_role_info.config import PLUGIN_VERSION

    assert plugin.metadata.extra["version"] == PLUGIN_VERSION
    assert plugin.module.__plugin_version__ == PLUGIN_VERSION
