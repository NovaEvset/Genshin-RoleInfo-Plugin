"""集成测试用的 NoneBot2 实例。

以反向 WebSocket 方式启动，等待 OneBot v11 协议端（Matcha / NapCat / 测试驱动）连入。

启动::

    cd <repo root>
    .venv/bin/python tests/integration/bot.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# 必须在 nonebot.init 之前设置，才能让插件的路径中心使用测试数据目录
os.environ.setdefault("ENVIRONMENT", "prod")
os.environ.setdefault(
    "GENSHIN_ROLE_INFO_DATA_DIR", str(ROOT / "tests" / "_integration_data")
)

import nonebot  # noqa: E402
from nonebot.adapters.onebot.v11 import Adapter  # noqa: E402

# 读取 tests/integration/bot.env（等价于 nonebot 的 .env 机制）
nonebot.init(_env_file=str(Path(__file__).parent / "bot.env"))

driver = nonebot.get_driver()
driver.register_adapter(Adapter)

# 加载本插件
nonebot.load_plugin("nonebot_plugin_genshin_role_info")

# 便于驱动脚本判断服务已就绪
@driver.on_startup
async def _announce() -> None:
    nonebot.logger.info(
        "集成测试实例已就绪: http://{}:{}  ws://{}:{}/onebot/v11/ws".format(
            driver.config.host,
            driver.config.port,
            driver.config.host,
            driver.config.port,
        )
    )


if __name__ == "__main__":
    nonebot.run()
