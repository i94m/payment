"""共享的 httpx.AsyncClient — 上游插件统一出口。

超时来自配置（connect 5s / read 15s），全进程复用连接池。
插件内禁止自建 client。
"""
import httpx

from app.core.config import config

_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=config.HTTP_CONNECT_TIMEOUT,
                read=config.HTTP_READ_TIMEOUT,
                write=config.HTTP_CONNECT_TIMEOUT,
                pool=config.HTTP_CONNECT_TIMEOUT,
            ),
            follow_redirects=False,
        )
    return _client


async def close_http_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None
