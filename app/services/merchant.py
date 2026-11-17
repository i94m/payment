"""商户公共业务。"""

import random
import secrets
from decimal import Decimal

from sqlmodel import select

from app.models import Merchant

CENT = Decimal("0.01")

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


def random_money(merchant: Merchant) -> Decimal:
    """按商户随机金额配置生成实付金额。

    - random_integer 开启：区间内只随机整数（1 ~ 5）
    - 关闭：按分随机，支持两位小数（1.00 ~ 5.00）
    - min == max 时返回该固定值
    调用方需先确认 random_enable 且 min/max 均 > 0。
    """
    rmin, rmax = merchant.random_min, merchant.random_max
    if rmin > rmax:
        rmin, rmax = rmax, rmin
    if merchant.random_integer:
        lo, hi = int(rmin), int(rmax)
        value = random.randint(lo, hi) if hi > lo else lo
        return Decimal(value).quantize(CENT)
    # 以「分」为单位随机，避免浮点误差
    lo, hi = int((rmin * 100).to_integral_value()), int((rmax * 100).to_integral_value())
    cents = random.randint(lo, hi) if hi > lo else lo
    return (Decimal(cents) / 100).quantize(CENT)
