"""出站回调地址安全校验（SSRF 防护）。

平台会向商户配置的 notify_url 主动 POST（且带阶梯重试），如果只校验长度，
商户就能让平台反复访问内网（127.0.0.1 / 169.254.169.254 元数据 / 10.x / ::1 等）
并借响应差异探测服务存活 —— 典型 SSRF。

口径（fail-closed）：
- 只允许 http / https，且必须带 host，禁止 URL 内嵌账号密码
- host 是 IP 字面量：必须是全球可路由地址
- host 是域名：解析出的**每一个**地址都必须是全球可路由地址
- 域名解析失败 → 视为不安全（无法确认目标不可路由）

注意：DNS 校验与真正连接之间仍有 TOCTOU 窗口（DNS rebinding），因此调用方还必须
禁止跟随 3xx 跳转（follow_redirects=False），不给「公网域名 302 到内网」留口子。
"""
import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

ALLOWED_SCHEMES = ("http", "https")


class UnsafeUrlError(ValueError):
    """目标地址不允许出站访问。"""


def _assert_public_ip(raw: str) -> None:
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError as e:
        raise UnsafeUrlError(f"无法解析的目标地址: {raw}") from e
    # is_global 一次覆盖私有/环回/链路本地/保留/组播/未指定/文档用途等网段
    if not ip.is_global:
        raise UnsafeUrlError(f"目标地址不是公网地址: {ip}")


async def assert_public_http_url(url: str) -> None:
    """校验出站地址；不安全时抛 UnsafeUrlError。"""
    try:
        parts = urlsplit((url or "").strip())
    except Exception as e:
        raise UnsafeUrlError(f"地址格式错误: {e}") from e

    if parts.scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"仅支持 http/https: {parts.scheme or '(空)'}")
    host = parts.hostname
    if not host:
        raise UnsafeUrlError("地址缺少域名")
    if parts.username or parts.password:
        raise UnsafeUrlError("地址不允许内嵌账号密码")

    try:
        _assert_public_ip(host)
        return  # IP 字面量，已校验
    except UnsafeUrlError:
        # 是 IP 字面量但不合规 → 直接拒绝；是域名 → 走解析校验
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise

    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except Exception as e:
        raise UnsafeUrlError(f"域名解析失败: {host}") from e
    if not infos:
        raise UnsafeUrlError(f"域名无可用地址: {host}")
    for info in infos:
        sockaddr = info[4]
        if sockaddr:
            _assert_public_ip(str(sockaddr[0]))
