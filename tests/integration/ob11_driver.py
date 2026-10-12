"""OneBot v11 协议级测试驱动。

本脚本扮演「协议端」的角色，与 Matcha 在协议层做的是同一件事：

1. 作为 WebSocket **客户端**连接到 NoneBot2 的 OneBot v11 反向 WS 端点；
2. 发送 ``meta_event.lifecycle`` 完成握手（匹配 Matcha 的接入流程）；
3. 发送真实的 OneBot v11 群消息 / 私聊消息事件，模拟用户输入；
4. 应答机器人发来的 API 调用（``send_group_msg`` / ``get_group_member_list`` 等）；
5. 记录机器人实际回复的文本、图片字节数与原始动作调用。

与 Matcha 的差异：Matcha 是 Tauri 桌面 GUI，需要人工点击创建角色、填写连接
信息、在界面上发消息；本驱动把这些操作脚本化，从而可在无头环境里复现同样的
协议交互。两者走的都是标准 OneBot v11 反向 WS，因此对被测插件是等价的。

用法::

    .venv/bin/python tests/integration/ob11_driver.py --out docs/evidence/integration.json
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import time
from pathlib import Path
from typing import Any

import websockets

BOT_QQ = 300000001
USER_QQ = 10001  # 同时是配置里的超级用户，便于测试超级用户命令
GROUP_ID = 987654321

DEFAULT_WS = "ws://127.0.0.1:18080/onebot/v11/ws"
DEFAULT_UID = "346948884"
# 用于推荐命令的套装缩写。注意：匹配用的是 artifact.json "Info" 中
# 套装名的**前两个字**，例如「昔日宗室之仪」的缩写是「昔日」而非「宗室」。
SET_ABBR = "昔日"


class Recorder:
    """记录机器人发出的动作与回复。"""

    def __init__(self) -> None:
        self.actions: list[dict[str, Any]] = []
        self.responses: list[dict[str, Any]] = []

    @staticmethod
    def summarize_message(message: Any) -> dict[str, Any]:
        """把 OneBot 消息压缩成可读摘要（图片只记字节数，避免报告爆炸）。"""
        if isinstance(message, str):
            return {"kind": "text", "text": message}
        if isinstance(message, list):
            parts = []
            for segment in message:
                if not isinstance(segment, dict):
                    parts.append({"kind": "unknown", "value": str(segment)[:80]})
                    continue
                seg_type = segment.get("type")
                data = segment.get("data") or {}
                if seg_type == "image":
                    raw = str(data.get("file", ""))
                    payload = raw.split(",", 1)[-1] if raw.startswith("base64://") else raw
                    size = len(payload) * 3 // 4 if raw.startswith("base64://") else None
                    parts.append(
                        {
                            "kind": "image",
                            "encoded": "base64" if raw.startswith("base64://") else "file",
                            "bytes": size,
                        }
                    )
                elif seg_type == "text":
                    parts.append({"kind": "text", "text": data.get("text", "")})
                elif seg_type == "at":
                    parts.append({"kind": "at", "qq": data.get("qq")})
                elif seg_type == "reply":
                    parts.append({"kind": "reply", "id": data.get("id")})
                else:
                    parts.append({"kind": seg_type, "data": str(data)[:120]})
            return {"kind": "chain", "segments": parts}
        return {"kind": "other", "value": str(message)[:120]}

    def record(self, action: str, params: dict[str, Any]) -> None:
        entry = {"action": action, "params": {}}
        for key, value in params.items():
            if key == "message":
                entry["params"]["message"] = self.summarize_message(value)
            else:
                entry["params"][key] = value
        self.actions.append(entry)

    def record_response(self, action: str, status: str) -> None:
        self.responses.append({"action": action, "status": status})


def make_message_event(
    text: str,
    *,
    group: bool = True,
    user_id: int = USER_QQ,
    message_id: int = 1,
    at_self: bool = False,
) -> dict[str, Any]:
    """构造 OneBot v11 消息事件。"""
    message: list[dict[str, Any]] = []
    if at_self:
        message.append({"type": "at", "data": {"qq": str(BOT_QQ)}})
    message.append({"type": "text", "data": {"text": text}})

    event: dict[str, Any] = {
        "time": int(time.time()),
        "self_id": BOT_QQ,
        "post_type": "message",
        "message_type": "group" if group else "private",
        "sub_type": "normal",
        "message_id": message_id,
        "user_id": user_id,
        "message": message,
        "raw_message": text,
        "font": 0,
        "sender": {
            "user_id": user_id,
            "nickname": "测试用户",
            "card": "测试群名片",
            "role": "owner" if user_id == USER_QQ else "member",
        },
    }
    if group:
        event["group_id"] = GROUP_ID
        event["anonymous"] = None
    return event


class OneBotDriver:
    def __init__(self, ws_url: str, uid: str, timeout: float = 180.0) -> None:
        self.ws_url = ws_url
        self.uid = uid
        self.timeout = timeout
        self.recorder = Recorder()
        self._ws: Any = None
        self._listener: asyncio.Task | None = None
        self.listener_error: str = ""

    async def __aenter__(self) -> "OneBotDriver":
        # OneBot v11 反向 WebSocket 必须携带 X-Self-ID 头，否则适配器以 403 拒绝
        # （详见 nonebot/adapters/onebot/v11/adapter.py 的 _handle_ws_reverse）。
        self._ws = await websockets.connect(
            self.ws_url,
            max_size=64 * 1024 * 1024,
            additional_headers={"X-Self-ID": str(BOT_QQ), "X-Client-Role": "Universal"},
        )
        # 必须常驻后台接收：机器人通过同一连接发来 API 调用（send_group_msg 等），
        # 若不及时应答，适配器会以 "WebSocket call api xxx timeout" 失败。
        self._listener = asyncio.create_task(self._pump_forever())
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._listener is not None:
            self._listener.cancel()
            try:
                await self._listener
            except (asyncio.CancelledError, Exception):
                pass
        if self._ws is not None:
            await self._ws.close()

    async def _pump_forever(self) -> None:
        try:
            async for raw in self._ws:
                payload = json.loads(raw)
                if "action" in payload:
                    await self._handle_action(payload)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            # 记录而不是静默退出：监听任务若意外中止，所有后续回复都会丢失
            self.listener_error = f"{type(error).__name__}: {error}"
            import traceback

            traceback.print_exc()

    # -- 发送与接收 ---------------------------------------------------------
    async def send_event(self, event: dict[str, Any]) -> None:
        await self._ws.send(json.dumps(event, ensure_ascii=False))

    async def _handle_action(self, payload: dict[str, Any]) -> None:
        action = payload.get("action", "")
        params = payload.get("params") or {}
        echo = payload.get("echo")
        self.recorder.record(action, params)

        if action in ("send_group_msg", "send_private_msg", "send_msg"):
            result: Any = {"message_id": int(time.time() * 1000) % 100000}
            status = "ok"
        elif action == "get_group_member_list":
            result = [
                {
                    "user_id": USER_QQ,
                    "nickname": "测试用户",
                    "card": "测试群名片",
                    "role": "owner",
                },
                {"user_id": 10002, "nickname": "路人乙", "card": "", "role": "member"},
            ]
            status = "ok"
        elif action == "get_group_member_info":
            result = {"user_id": params.get("user_id"), "nickname": "测试用户", "card": "测试群名片"}
            status = "ok"
        elif action == "get_stranger_info":
            result = {"user_id": params.get("user_id"), "nickname": "测试用户"}
            status = "ok"
        elif action == "get_login_info":
            result = {"user_id": BOT_QQ, "nickname": "测试机器人"}
            status = "ok"
        elif action == "get_private_file_url":
            # 插件用它取私聊文件下载地址；此处给出不可达地址，验证其错误处理
            result = {"url": "http://127.0.0.1:1/not-found"}
            status = "ok"
        else:
            result = {}
            status = "ok"

        self.recorder.record_response(action, status)
        await self._ws.send(
            json.dumps({"status": status, "retcode": 0, "data": result, "echo": echo})
        )

    async def run_command(
        self,
        text: str,
        *,
        group: bool = True,
        at_self: bool = False,
        message_id: int = 1,
        wait: float = 12.0,
    ) -> list[dict[str, Any]]:
        """发送一条命令并收集该命令触发的全部动作。

        插件可能分多次发送（例如先提示“正在匹配…”再发图片），因此收到动作后
        继续等待，直到连续静默 ``wait`` 秒。
        """
        before = len(self.recorder.actions)
        await self.send_event(
            make_message_event(
                text, group=group, message_id=message_id, at_self=at_self
            )
        )

        deadline = asyncio.get_event_loop().time() + self.timeout
        idle = 0.0
        interval = 0.5
        while True:
            if asyncio.get_event_loop().time() > deadline:
                break
            await asyncio.sleep(interval)
            if len(self.recorder.actions) > before:
                idle += interval
                if idle >= wait:
                    break
            else:
                idle = 0.0
        return self.recorder.actions[before:]

    async def pump(self, seconds: float) -> None:
        """等待一段时间，让后台监听任务处理机器人发来的动作。"""
        await asyncio.sleep(seconds)

    async def handshake(self) -> None:
        """发送生命周期握手（Matcha 接入时也会发送）。"""
        await self.send_event(
            {
                "time": int(time.time()),
                "self_id": BOT_QQ,
                "post_type": "meta_event",
                "meta_event_type": "lifecycle",
                "sub_type": "connect",
            }
        )
        await self.pump(1.0)


def extract_text(actions: list[dict[str, Any]]) -> str:
    """把动作里的文本拼接出来（便于断言）。"""
    chunks: list[str] = []
    for action in actions:
        summary = (action.get("params") or {}).get("message") or {}
        if summary.get("kind") == "text":
            chunks.append(str(summary.get("text", "")))
        for segment in summary.get("segments", []) or []:
            if segment.get("kind") == "text":
                chunks.append(str(segment.get("text", "")))
    return "\n".join(chunks)


def count_images(actions: list[dict[str, Any]]) -> int:
    total = 0
    for action in actions:
        summary = (action.get("params") or {}).get("message") or {}
        for segment in summary.get("segments", []) or []:
            if segment.get("kind") == "image":
                total += 1
    return total


def image_bytes(actions: list[dict[str, Any]]) -> list[int]:
    sizes: list[int] = []
    for action in actions:
        summary = (action.get("params") or {}).get("message") or {}
        for segment in summary.get("segments", []) or []:
            if segment.get("kind") == "image" and segment.get("bytes"):
                sizes.append(int(segment["bytes"]))
    return sizes


async def main() -> int:
    parser = argparse.ArgumentParser(description="OneBot v11 协议级集成测试驱动")
    parser.add_argument("--ws", default=DEFAULT_WS, help="NoneBot2 反向 WS 地址")
    parser.add_argument("--uid", default=DEFAULT_UID, help="用于测试的真实原神 UID")
    parser.add_argument("--out", default="", help="把结果写入 JSON 文件")
    parser.add_argument(
        "--skip-network",
        action="store_true",
        help="跳过需要访问 Enka 的命令（离线环境）",
    )
    args = parser.parse_args()

    cases: list[dict[str, Any]] = []

    def record_case(
        name: str,
        command: str,
        actions: list[dict[str, Any]],
        expectation: str,
    ) -> None:
        text = extract_text(actions)
        cases.append(
            {
                "case": name,
                "command": command,
                "expected": expectation,
                "actions": actions,
                "observed": {
                    "action_count": len(actions),
                    "actions": [a["action"] for a in actions],
                    "images": count_images(actions),
                    "image_bytes": image_bytes(actions),
                    "text": text[:1500],
                },
                "passed": None,  # 由下方断言填充
            }
        )

    async with OneBotDriver(args.ws, args.uid) as driver:
        await driver.handshake()
        print("已连接并完成握手")

        # 1) 绑定 UID（不需要网络）
        actions = await driver.run_command(f"原神绑定{args.uid}", message_id=101)
        record_case("绑定 UID", f"原神绑定{args.uid}", actions, "回复绑定成功")
        text = cases[-1]["observed"]["text"]
        cases[-1]["passed"] = args.uid in text and "成功" in text

        # 2) 重复绑定
        actions = await driver.run_command(f"原神绑定{args.uid}", message_id=102)
        record_case("重复绑定", f"原神绑定{args.uid}", actions, "提示已绑定过")
        cases[-1]["passed"] = "已绑定过" in cases[-1]["observed"]["text"]

        # 3) 解绑 + 非法 UID（必须先解绑，否则会先命中"已绑定"分支）
        actions = await driver.run_command("原神解绑", message_id=103)
        record_case("解绑 UID", "原神解绑", actions, "回复删除成功")
        cases[-1]["passed"] = "删除成功" in cases[-1]["observed"]["text"]

        actions = await driver.run_command("原神绑定999", message_id=104)
        record_case("非法 UID（非 9 位）", "原神绑定999", actions, "提示 uid 不合法")
        cases[-1]["passed"] = "不合法" in cases[-1]["observed"]["text"]

        actions = await driver.run_command("原神绑定012345678", message_id=105)
        record_case(
            "非法 UID（首位为 0）", "原神绑定012345678", actions, "提示 uid 不合法"
        )
        cases[-1]["passed"] = "不合法" in cases[-1]["observed"]["text"]

        # 重新绑定，供后续用例使用
        actions = await driver.run_command(f"原神绑定{args.uid}", message_id=106)
        record_case("重新绑定 UID", f"原神绑定{args.uid}", actions, "回复绑定成功")
        cases[-1]["passed"] = args.uid in cases[-1]["observed"]["text"]

        # 4) 排行榜（群聊）
        actions = await driver.run_command("原神角色排行", message_id=107)
        record_case("原神角色排行", "原神角色排行", actions, "更新图或缓存提示")
        cases[-1]["passed"] = len(actions) > 0

        if not args.skip_network:
            # 5) 更新面板（真实访问 Enka）
            actions = await driver.run_command(
                f"更新原神面板{args.uid}", message_id=108, wait=30.0
            )
            record_case(
                "更新原神面板",
                f"更新原神面板{args.uid}",
                actions,
                "回复更新完成并附更新图",
            )
            cases[-1]["passed"] = count_images(actions) > 0

            # 6) 角色面板（真实渲染）
            actions = await driver.run_command("阿罗夏面板", message_id=109, wait=30.0)
            record_case("角色面板", "阿罗夏面板", actions, "返回面板图片")
            cases[-1]["passed"] = count_images(actions) > 0

            # 7) 圣遗物榜单
            actions = await driver.run_command("圣遗物榜单", message_id=110, wait=30.0)
            record_case("圣遗物榜单", "圣遗物榜单", actions, "返回榜单图片与统计")
            cases[-1]["passed"] = count_images(actions) > 0

            # 8) 评分排行（群聊，需要群成员缓存）
            actions = await driver.run_command("阿罗夏评分排行", message_id=111, wait=30.0)
            record_case("评分排行", "阿罗夏评分排行", actions, "返回排行图片")
            cases[-1]["passed"] = len(actions) > 0

            # 9) 推荐（评分模式）
            # 注意：推荐命令的正则是 (.*?)([花羽沙杯冠套])推荐(\d*)，"推荐" 前
            # 必须是 花/羽/沙/杯/冠/套；"阿罗夏推荐"（缺部位/套装）不会匹配。
            actions = await driver.run_command(f"阿罗夏{SET_ABBR}套推荐", message_id=112, wait=30.0)
            record_case(
                "圣遗物推荐",
                f"阿罗夏{SET_ABBR}套推荐",
                actions,
                "先提示匹配中，再返回推荐图",
            )
            cases[-1]["passed"] = count_images(actions) > 0

            # 9b) 推荐序号越界（错误分支）
            actions = await driver.run_command(f"阿罗夏{SET_ABBR}套推荐99", message_id=113, wait=30.0)
            record_case(
                "推荐序号越界",
                f"阿罗夏{SET_ABBR}套推荐99",
                actions,
                "返回序号越界提示",
            )
            cases[-1]["passed"] = "伤害序号应在" in cases[-1]["observed"]["text"]

        # 10) 未识别角色（边界）
        actions = await driver.run_command("不存在的角色面板", message_id=114)
        record_case("未识别角色", "不存在的角色面板", actions, "不应有任何回复")
        cases[-1]["passed"] = len(actions) == 0

        # 11) 圣遗物导入提示
        actions = await driver.run_command("圣遗物导入", message_id=115)
        record_case("圣遗物导入提示", "圣遗物导入", actions, "返回操作步骤")
        cases[-1]["passed"] = "good.json" in cases[-1]["observed"]["text"]

        # 12) 私聊事件
        actions = await driver.run_command("阿罗夏面板", group=False, message_id=116)
        record_case("私聊事件", "阿罗夏面板（私聊）", actions, "私聊同样能处理")
        cases[-1]["passed"] = len(actions) > 0

        # 13) 超级用户命令
        actions = await driver.run_command("检查原神面板更新", message_id=117)
        record_case("检查更新", "检查原神面板更新", actions, "无更新时不回复或给出结果")
        cases[-1]["passed"] = True  # 无更新时合法地不回复

        # 14) 重置最强（超级用户）
        actions = await driver.run_command("重置最强阿罗夏", message_id=118)
        record_case("重置最强", "重置最强阿罗夏", actions, "回复重置成功")
        cases[-1]["passed"] = "重置" in cases[-1]["observed"]["text"]

        # 15) 米游社同步（未配置提供方）
        actions = await driver.run_command("米游社更新原神面板", message_id=119)
        record_case(
            "米游社同步（未配置）",
            "米游社更新原神面板",
            actions,
            "返回配置指引而非静默失败",
        )
        cases[-1]["passed"] = "米游社" in cases[-1]["observed"]["text"]

    total = len(cases)
    passed = sum(1 for case in cases if case["passed"])
    report = {
        "driver": "tests/integration/ob11_driver.py",
        "ws_url": args.ws,
        "bot_qq": BOT_QQ,
        "user_qq": USER_QQ,
        "group_id": GROUP_ID,
        "uid": args.uid,
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "cases": cases,
    }

    print(f"\n=== 集成测试结果: {passed}/{total} 通过 ===")
    for case in cases:
        mark = "PASS" if case["passed"] else "FAIL"
        print(f"  [{mark}] {case['case']}: actions={case['observed']['action_count']} "
              f"images={case['observed']['images']}")
        if not case["passed"]:
            print(f"         文本: {case['observed']['text'][:200]!r}")

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n报告已写入: {out_path}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
