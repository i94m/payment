"""商户公共业务。"""

import secrets

from sqlmodel import select

from app.models import Merchant

# 对外商户号：8 位随机数字，避开 1000 系列本地/演示号段。
PID_MIN = 10_000_000
PID_RANGE = 90_000_000


async def generate_merchant_pid(session, attempts: int = 8) -> int:
    """生成一个未占用的随机商户号；并发场景仍由数据库唯一索引兜底。"""
    for _ in range(attempts):
        pid = PID_MIN + secrets.randbelow(PID_RANGE)
        exists = (await session.exec(
            select(Merchant.pid).where(Merchant.pid == pid)
        )).first()
        if exists is None:
            return pid
    raise RuntimeError("随机商户号生成冲突，请重试")
