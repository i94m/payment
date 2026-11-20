"""商户端测试支付：与管理端「通道测试」同一套逻辑，商户侧全程无感。

链路与正常收款一致：建单 → 通道路由选路 → 上游下单 → 回调验签 → 订单置已支付，
所以能一次验证「插件协议 / 通道凭证 / 回调处理」三件事。

订单与通道测试一样挂在系统测试商户名下（order_type=2）：测试单不进商户自己的
订单记录/资金明细/统计，商户面板上不出现任何测试单痕迹。
- 不入余额/分润/结算/邀请返现，不投递商户通知，不进任何统计口径
- 不占通道日限额与限速窗口（测试不该把通道测到自动关闭）
"""
import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlmodel import col, select

from app.core.deps import CacheDep, SessionDep
from app.core.limiter import rate_limit
from app.core.security import MerchantUser
from app.models import PaymentType
from app.responses import err, suc
from app.services.balance import dmoney
from app.services.channel import list_available_pay_types
from app.services.order import create_test_order, process_payment
from app.services.settings import get_all_settings

_log = logging.getLogger(__name__)

router = APIRouter()

# 与通道测试同口径（0.01 ~ 1000 元）
TEST_PAY_MIN = "0.01"
TEST_PAY_MAX = "1000.00"


@router.get("/types")
async def testpay_types(session: SessionDep, auth: MerchantUser):
    """可选支付方式：启用且有可用通道（与收银台同口径）。"""
    return suc(await list_available_pay_types(session))


class TestPayRequest(BaseModel):
    type: str = "alipay"
    amount: str = "1.00"
    name: str = "支付测试"


@router.post("/submit", dependencies=[rate_limit(times=20)])
async def submit_testpay(body: TestPayRequest, request: Request, session: SessionDep,
                         cache: CacheDep, auth: MerchantUser):
    """发起一笔测试支付，返回上游支付信息（链接/二维码/HTML）。

    与管理端通道测试一致：测试单挂在内置「系统测试」商户名下（order_type=2），
    商户自己的订单记录/资金/统计完全不受影响。
    """
    from app.services.test_pay import get_test_merchant

    try:
        amount = dmoney(body.amount)
    except Exception:
        return err(-1, "金额格式错误")
    if amount < dmoney(TEST_PAY_MIN) or amount > dmoney(TEST_PAY_MAX):
        return err(-1, f"金额需在 {TEST_PAY_MIN} ~ {TEST_PAY_MAX} 元之间")

    pt = (await session.exec(
        select(PaymentType).where(PaymentType.code == body.type, col(PaymentType.status) == 1)
    )).first()
    if not pt:
        return err(-1, "支付方式不存在或未启用")

    settings = await get_all_settings(session, cache)
    site = (settings.get("site_url") or str(request.base_url)).rstrip("/")

    merchant = await get_test_merchant(session)
    order = await create_test_order(
        session,
        merchant=merchant,
        payment_type=pt,
        money=amount,
        subject=body.name or "支付测试",
        client_ip=request.client.host if request.client else "",
        # 支付完成回跳商户端首页（订单不属于该商户，不回订单记录）
        return_url=f"{site}/merchant/#/",
        cache=cache,
    )
    flow = await process_payment(
        order, merchant, session, cache,
        notify_base=site, return_base=site, site_url=site,
    )
    if not flow.ok:
        _log.warning("商户测试支付失败 merchant_id=%s trade_no=%s error=%s",
                     auth.user.id, order.trade_no, flow.error)
        return err(-1, flow.error or "获取支付接口数据失败", errors=[{
            "trade_no": order.trade_no,
            "upstream_request": getattr(flow.plugin, "last_debug", None),
        }])

    return suc({
        "trade_no": order.trade_no,
        "pay_type": flow.pay_type,
        "pay_info": flow.pay_info,
    })
