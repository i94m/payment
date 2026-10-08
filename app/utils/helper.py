"""通用工具函数"""

import os

from fastapi import Request


async def get_real_ip(request: Request) -> str:
    """
    获取真实客户端 IP

    优先级: CF-Connecting-IP > X-Forwarded-For > client.host
    """
    ip = request.headers.get("CF-Connecting-IP")
    if not ip:
        x_forwarded_for = request.headers.get("X-Forwarded-For")
        if x_forwarded_for:
            ip = x_forwarded_for.split(",")[0].strip()
        else:
            ip = request.client.host if request.client else ""
    return str(ip)


def get_private_abs_path(path: str | None = None) -> str | None:
    """获取私有文件的绝对路径"""
    if not path:
        return None
    return os.path.abspath(f"storage/private/{path}")
