"""插件资源路径与用户数据目录。

本模块是整个插件唯一的路径来源。移植自真寻 Bot 版本时，原先分散在各处的
``Path(__file__).resolve().parents[N]`` 与 ``os.path.dirname(__file__)``
计算全部收敛到这里，避免移动模块后路径静默失效。

两类目录严格区分：

* **内置资源目录** (:data:`RES_PATH`)：随插件分发的只读素材（字体、背景图、
  JSON 数据表）。永远位于插件包内部，不可被配置覆盖。
* **用户数据目录** (:data:`DATA_PATH`)：运行时产生的缓存与用户数据
  （UID 绑定、角色缓存、群榜单、下载的图标）。可通过环境变量
  ``GENSHIN_ROLE_INFO_DATA_DIR`` 重定向到持久化目录，便于容器化部署。
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = [
    "BASE_PATH",
    "RES_PATH",
    "DATA_PATH",
    "DATA_ENV_VAR",
    "get_data_path",
    "ensure_data_dirs",
    "json_path",
    "bg_path",
    "other_path",
    "outline_path",
    "regoin_path",
    "font_path",
    "player_info_path",
    "group_info_path",
    "data_res_path",
    "char_pic_path",
    "avatar_path",
    "skill_path",
    "talent_path",
    "weapon_path",
    "reli_path",
    "qq_logo_path",
]

# --------------------------------------------------------------------------
# 内置资源
# --------------------------------------------------------------------------
BASE_PATH = Path(__file__).resolve().parent

RES_PATH = BASE_PATH / "res"

json_path = RES_PATH / "json_data"
bg_path = RES_PATH / "background"
other_path = RES_PATH / "other"
outline_path = RES_PATH / "outline"
regoin_path = RES_PATH / "region"
font_path = RES_PATH / "fonts"

# ``card_utils`` 以字符串拼接方式使用这些常量，统一转成 str 以保持兼容。
json_path = str(json_path)
bg_path = str(bg_path)
other_path = str(other_path)
outline_path = str(outline_path)
regoin_path = str(regoin_path)
font_path = str(font_path)

# --------------------------------------------------------------------------
# 用户数据
# --------------------------------------------------------------------------
DATA_ENV_VAR = "GENSHIN_ROLE_INFO_DATA_DIR"


def get_data_path() -> Path:
    """返回用户数据根目录。

    优先读取环境变量 ``GENSHIN_ROLE_INFO_DATA_DIR``；未设置或设置无效时
    回退到插件包内的 ``user_data`` 目录。
    """
    override = os.environ.get(DATA_ENV_VAR, "").strip()
    if override:
        try:
            return Path(override).expanduser().resolve()
        except (OSError, RuntimeError):  # pragma: no cover - 极端路径异常
            pass
    return BASE_PATH / "user_data"


DATA_PATH = get_data_path()

player_info_path = str(DATA_PATH / "player_info")
group_info_path = str(DATA_PATH / "group_info")
data_res_path = str(DATA_PATH / "res")
qq_logo_path = str(DATA_PATH / "qq_logo")

# ``data_res_path`` 下的子目录：运行时按需下载的角色立绘、图标等。
char_pic_path = str(DATA_PATH / "res" / "character")
avatar_path = str(DATA_PATH / "res" / "avatar")
skill_path = str(DATA_PATH / "res" / "skill")
talent_path = str(DATA_PATH / "res" / "talent")
weapon_path = str(DATA_PATH / "res" / "weapon")
reli_path = str(DATA_PATH / "res" / "reli")


def ensure_data_dirs() -> Path:
    """创建用户数据目录树，返回数据根目录。

    插件的存储层在写入时会自行创建父目录，因此这里主要用于启动期
    的显式初始化与部署自检。
    """
    for directory in (
        DATA_PATH,
        DATA_PATH / "player_info",
        DATA_PATH / "group_info",
        DATA_PATH / "res" / "character",
        DATA_PATH / "res" / "avatar",
        DATA_PATH / "res" / "skill",
        DATA_PATH / "res" / "talent",
        DATA_PATH / "res" / "weapon",
        DATA_PATH / "res" / "reli",
        DATA_PATH / "qq_logo",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return DATA_PATH
