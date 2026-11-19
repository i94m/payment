"""测试支付（order_type=2）的公共支撑：系统测试商户。

老 epay 的测试单是真实落库、真实走回调的，问题只在于它没有任何「这是测试」的标记：
测试单混进商户余额、通道限额和各项统计。这里沿用「真实订单 + 完整回调」的链路，
但把类型标记挂在 order_type 上（见 services/order.py 的 ORDER_TYPE_TEST），
资金/统计/限额/通知全部按类型排除。

管理端「通道测试」的订单挂在内置测试商户名下（对应老系统的 test_pay_uid），
无需运维预先配置商户号：首次使用自动创建，之后复用。
"""
import secrets

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.security import hash_password
from app.models import Merchant

# 6 位号段：与随机 8 位商户号（PID_MIN=10_000_000）、1000 系列演示号都不冲突
TEST_MERCHANT_PID = 100_000
TEST_MERCHANT_NAME = "系统测试"


async def get_test_merchant(session: AsyncSession) -> Merchant:
    """取内置测试商户（不存在则创建；并发创建由 pid 唯一键兜底）。

    该商户状态为禁用、支付/结算均关闭：不能登录、也不能通过开放 API 下单，
    只作为管理端测试单的挂载点。商户列表/商户数统计已排除它。
    """
    merchant = (await session.exec(
        select(Merchant).where(Merchant.pid == TEST_MERCHANT_PID)
    )).first()
    if merchant:
        return merchant

    merchant = Merchant(
        pid=TEST_MERCHANT_PID,
        username=TEST_MERCHANT_NAME,
        password=hash_password(secrets.token_urlsafe(32)),
        status=0,
        pay_status=0,
        settle_status=0,
    )
    session.add(merchant)
    try:
        await session.commit()
    except Exception:
        # 并发首次测试：另一边已经建好了，直接复用
        await session.rollback()
        merchant = (await session.exec(
            select(Merchant).where(Merchant.pid == TEST_MERCHANT_PID)
        )).first()
        if not merchant:
            raise
        return merchant
    await session.refresh(merchant)
    return merchant
