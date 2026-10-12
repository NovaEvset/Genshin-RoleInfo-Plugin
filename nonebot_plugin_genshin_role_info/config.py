"""插件配置项。

所有可变配置均通过 NoneBot2 的 ``.env`` / ``.env.prod`` 注入，不写入源码。
配置类继承 :class:`pydantic.BaseSettings`，NoneBot2 会在加载插件时自动读取
``extra="ignore"`` 允许同一配置文件包含其它插件的配置。
"""

from __future__ import annotations

from nonebot import get_driver
from pydantic import BaseModel, Field

__all__ = ["Config", "config", "PLUGIN_VERSION", "PLUGIN_NAME"]

PLUGIN_NAME = "原神角色面板"
PLUGIN_VERSION = "4.3.13"


class Config(BaseModel):
    """NoneBot2 插件配置。

    与真寻版本的差异：真寻使用自带配置中心，本插件改用 NoneBot2 的环境变量
    配置机制；除此之外的可调行为保持一致。
    """

    # ---- 数据与存储 ----
    genshin_role_info_data_dir: str = Field(
        default="",
        description=(
            "用户数据根目录（UID 绑定、角色缓存、群榜单、下载的图标）。"
            "留空则使用插件包内的 user_data 目录。"
        ),
    )

    # ---- 数据获取 ----
    genshin_role_info_api_urls: str = Field(
        default=(
            "https://profile.microgg.cn/api/uid/{},"
            "https://enka.network/api/uid/{}"
        ),
        description=(
            "角色橱窗数据接口，逗号分隔，按顺序回退。"
            "占位符为位置式 {}（与源实现一致），会被替换为 UID；"
            "也接受 {uid} 写法。"
        ),
    )
    genshin_role_info_request_timeout: float = Field(
        default=120.0,
        gt=0,
        description="单次橱窗数据请求的整体超时（秒）。",
    )

    # ---- 更新检查 ----
    genshin_role_info_check_update: bool = Field(
        default=True,
        description="是否启用每日一次的插件更新检查（需要 nonebot-plugin-apscheduler）。",
    )
    genshin_role_info_version_url: str = Field(
        default=(
            "https://raw.githubusercontent.com/NovaEvset/"
            "Genshin-RoleInfo-Plugin/main/nonebot_plugin_genshin_role_info/__init__.py"
        ),
        description="远端版本号检查地址。",
    )
    genshin_role_info_changelog_url: str = Field(
        default=(
            "https://raw.githubusercontent.com/NovaEvset/"
            "Genshin-RoleInfo-Plugin/main/README.md"
        ),
        description="远端更新说明地址。",
    )

    # ---- 米游社同步 ----
    genshin_role_info_mys_api_base: str = Field(
        default="https://api-takumi-record.mihoyo.com",
        description="米游社战绩接口基址（可选功能，需要已有登录态提供方）。",
    )


def _load_config() -> Config:
    """从 NoneBot2 全局配置中读取本插件配置。

    ``get_driver()`` 在插件导入期即可用；若因特殊加载顺序不可用，
    回退到默认配置而不是让插件加载失败。
    """
    try:
        driver = get_driver()
    except Exception:  # pragma: no cover - 非 NoneBot2 运行环境
        return Config()
    try:
        return Config(**dict(driver.config.model_dump()))
    except Exception:  # pragma: no cover - 配置项类型异常
        return Config()


config = _load_config()
