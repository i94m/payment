"""共享的 httpx.AsyncClient — 上游插件与商户通知统一出口。

两个 profile：
- 上游插件（get_http_client）：超时来自配置（connect 5s / read 15s），全进程复用连接池；
- 商户通知（get_notify_client）：单次投递 10s、不跟随 3xx（SSRF 口径与 url_guard 一致）。

插件内禁止自建 client；通知侧同理 —— 除非要保留「每次重试重新握手」的行为。
"""
import httpx

from app.core.config import config

# 商户通知单次投递超时（秒）。阶梯重试最坏 6 档，之前每档新建 client
# 就多一次 TCP/TLS 握手，共享连接池后同一商户域名只握一次（P3-30）。
NOTIFY_TIMEOUT = 10.0

_client: httpx.AsyncClient | None = None
_notify_client: httpx.AsyncClient | None = None


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


def get_notify_client() -> httpx.AsyncClient:
    """商户通知专用客户端（P3-30）：共享连接池，禁止跟随跳转。

    不跟随 3xx 与投递侧 SSRF 防护配套 —— 否则「公网域名 302 到内网」会绕过
    url_guard 的地址校验。
    """
    global _notify_client
    if _notify_client is None or _notify_client.is_closed:
        _notify_client = httpx.AsyncClient(
            timeout=httpx.Timeout(NOTIFY_TIMEOUT),
            follow_redirects=False,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _notify_client


async def close_http_client() -> None:
    """进程退出时关闭所有连接池（幂等，可重复调用）。"""
    global _client, _notify_client
    clients = (_client, _notify_client)
    _client = None
    _notify_client = None
    for client in clients:
        if client is None or client.is_closed:
            continue
        try:
            await client.aclose()
        except RuntimeError:
            # 连接池挂在已关闭的事件循环上（测试/反复重启）：直接丢弃，不影响退出
            pass
