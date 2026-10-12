"""pytest 公共配置。

关键顺序要求：:func:`nonebot.init` 与 :data:`GENSHIN_ROLE_INFO_DATA_DIR` 必须在
导入插件包之前完成，因为插件在导入期就会构建匹配器、读取配置并解析数据目录。
因此这些操作放在模块顶层，而不是 fixture 内部。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent

# 让 `import nonebot_plugin_genshin_role_info` 可用，并让 tests._helpers 可导入。
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

# 测试数据目录与生产数据隔离；必须在导入插件前设置。
DATA_DIR = TESTS_DIR / "_data"
os.environ.setdefault("GENSHIN_ROLE_INFO_DATA_DIR", str(DATA_DIR))
os.environ.setdefault("GENSHIN_ROLE_INFO_CHECK_UPDATE", "false")

import nonebot  # noqa: E402
from nonebot.adapters.onebot.v11 import Adapter  # noqa: E402

nonebot.init(
    driver="~none",
    log_level="ERROR",
    superusers={"10001", "10002"},
    genshin_role_info_check_update=False,
    genshin_role_info_data_dir=str(DATA_DIR),
)
driver = nonebot.get_driver()
driver.register_adapter(Adapter)

# 使用标准加载机制加载插件（与生产环境一致）。必须直接 load_plugin，
# 不能先 import 再 load（NoneBot 对已加载的插件会返回 None）。
PLUGIN = nonebot.load_plugin("nonebot_plugin_genshin_role_info")
assert PLUGIN is not None, "插件加载失败"
PLUGIN_MODULE = PLUGIN.module

# 真实 UID 与离线夹具（夹具由该 UID 的真实 Enka 响应生成）。
REAL_UID = "346948884"
FIXTURES = TESTS_DIR / "fixtures"


@pytest.fixture(scope="session")
def plugin():
    return PLUGIN


@pytest.fixture(scope="session")
def real_uid() -> str:
    return REAL_UID


@pytest.fixture(scope="session")
def data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


@pytest.fixture(scope="session")
def enka_fixture() -> dict:
    import json

    with (FIXTURES / f"enka_{REAL_UID}.json").open(encoding="utf-8") as file:
        return json.load(file)


@pytest.fixture(scope="session")
def player_cache_fixture() -> dict:
    import json

    with (FIXTURES / f"player_cache_{REAL_UID}.json").open(encoding="utf-8") as file:
        return json.load(file)


@pytest.fixture
def player_info_dir(data_dir: Path, monkeypatch):
    """把玩家的 UID 绑定与缓存目录指向临时目录。"""
    from nonebot_plugin_genshin_role_info.utils import card_utils

    directory = data_dir / "player_info"
    directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(card_utils, "player_info_path", str(directory))
    return directory


@pytest.fixture
def group_info_dir(data_dir: Path, monkeypatch):
    from nonebot_plugin_genshin_role_info.utils import card_utils

    directory = data_dir / "group_info"
    directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(card_utils, "group_info_path", str(directory))
    return directory
