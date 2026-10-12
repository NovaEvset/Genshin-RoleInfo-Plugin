"""异步 HTTP 客户端。

移植自真寻 Bot 的 ``zhenxun.utils.http_utils.AsyncHttpx``。原实现是 httpx
的薄封装，本模块以同样的调用签名重新实现，因此上层代码只需改变导入路径，
不需要改动调用方式。

设计要点：

* 进程内复用一个 :class:`httpx.AsyncClient`，避免为每张图标新建连接池。
* 连接/读取超时分开设置：图标与 JSON 体积差异较大，读取超时给得更宽松。
* 失败按「可重试」与「不可重试」分类：网络层异常与 5xx 会重试，4xx 属于
  确定性错误，立即放弃，避免无意义的重复请求。
* 重试次数固定且很少，绝不对同一 URL 做无上限重试。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx

__all__ = ["AsyncHttpx", "get_client", "close_client"]

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
        "NoneBot2-GenshinRoleInfo/4.3.13"
    )
}

CONNECT_TIMEOUT = 15.0
READ_TIMEOUT = 60.0
TOTAL_TIMEOUT = 180.0
RETRY_TIMES = 3
RETRY_INTERVAL = 0.5

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    """返回共享的 :class:`httpx.AsyncClient`（按需惰性创建）。"""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                TOTAL_TIMEOUT,
                connect=CONNECT_TIMEOUT,
                read=READ_TIMEOUT,
            ),
            headers=DEFAULT_HEADERS,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
        )
    return _client


async def close_client() -> None:
    """关闭共享客户端，供驱动关闭钩子调用。"""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


def _is_retryable(exc: Exception) -> bool:
    """5xx 与网络层异常可重试；4xx 是确定性错误，重试无意义。"""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return isinstance(
        exc,
        (
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
        ),
    )


def _is_client_error(exc: Exception) -> bool:
    """4xx：请求本身有问题，换 URL 或重试都不会成功。"""
    return isinstance(exc, httpx.HTTPStatusError) and (
        400 <= exc.response.status_code < 500
    )


class AsyncHttpx:
    """与真寻 ``AsyncHttpx`` 兼容的静态方法集合。"""

    @staticmethod
    async def get_content(
        url: str,
        *,
        retry_times: int = RETRY_TIMES,
        retry_interval: float = RETRY_INTERVAL,
        **kwargs: Any,
    ) -> bytes:
        """获取 URL 内容字节。

        :param url: 目标地址
        :param retry_times: 最大尝试次数（含首次）
        :param retry_interval: 两次尝试之间的基础间隔，按尝试次数线性退避
        :raises httpx.HTTPStatusError: 服务端返回非成功状态码
        :raises httpx.HTTPError: 网络层异常且重试耗尽
        """
        kwargs.setdefault("follow_redirects", True)
        attempts = max(1, retry_times)
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                response = await get_client().get(url, **kwargs)
                response.raise_for_status()
                return response.content
            except Exception as error:  # noqa: BLE001 - 需要分类处理
                if _is_client_error(error) or not _is_retryable(error):
                    raise
                last_error = error
            if attempt + 1 < attempts:
                await asyncio.sleep(retry_interval * (attempt + 1))
        assert last_error is not None
        raise last_error

    @staticmethod
    async def download_file(
        url: str | list[str],
        path: str | Path,
        *,
        retry_times: int = RETRY_TIMES,
        retry_interval: float = RETRY_INTERVAL,
        **kwargs: Any,
    ) -> bool:
        """流式下载到 ``path``，成功返回 ``True``，失败返回 ``False``。

        不抛出异常：调用方 ``download_file_checked`` 依据返回值与文件校验
        结果决定是否报错，并负责清理临时文件。

        :param url: 单个地址，或按顺序尝试的候选地址列表
        """
        urls = [url] if isinstance(url, str) else list(url)
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        kwargs.setdefault("follow_redirects", True)
        kwargs.pop("stream", None)
        attempts = max(1, retry_times)

        for url_index, candidate in enumerate(urls):
            for attempt in range(attempts):
                try:
                    async with get_client().stream(
                        "GET", candidate, **kwargs
                    ) as response:
                        response.raise_for_status()
                        with destination.open("wb") as file:
                            async for chunk in response.aiter_bytes():
                                file.write(chunk)
                    if destination.is_file() and destination.stat().st_size > 0:
                        return True
                    # 落盘成功但内容为空：按可重试处理。
                    destination.unlink(missing_ok=True)
                    if attempt + 1 < attempts:
                        await asyncio.sleep(retry_interval * (attempt + 1))
                    continue
                except Exception as caught:  # noqa: BLE001 - 统一转成布尔结果
                    destination.unlink(missing_ok=True)
                    if _is_client_error(caught) or not _is_retryable(caught):
                        # 该地址不可用（4xx）或错误不可重试：
                        # 立即尝试下一个候选地址（若有），而不是整体失败。
                        break
                    if attempt + 1 < attempts:
                        await asyncio.sleep(retry_interval * (attempt + 1))
            if url_index + 1 < len(urls):
                await asyncio.sleep(retry_interval)
        return False
