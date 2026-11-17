"""聚合收款页 API（/paypage）— 公开访问 + 限流。

免登录收款页：输金额（或商户开启随机金额时倒计时自动建单）
→ 建收款单 → 跳收银台支付。/p/{code} 短码映射行携带代理归属。
（页面本体 HTML 见 routers/web.py；随机金额商户 /p/{code} 由
web.py 服务端建单直跳，建单核心在本模块共用）
"""
import logging
import random
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlmodel import col, select

from app.core.deps import CacheDep, SessionDep
from app.core.limiter import rate_limit
from app.models import Agent, Merchant, PaymentType
from app.responses import err, suc_data
from app.services.balance import dmoney
from app.services.merchant import random_money
from app.services.order import create_order, process_payment
from app.lib.linktoken import cashier_url as build_cashier_url
from app.services import paytoken
from app.services.settings import get_all_settings, get_setting

_log = logging.getLogger(__name__)

router = APIRouter()


async def _load_merchant(session: SessionDep, merchant_id: int) -> Merchant | None:
    m = await session.get(Merchant, merchant_id)
    return m if m and m.status == 1 else None


def _random_ready(m: Merchant) -> bool:
    """随机金额配置有效（三要素齐全，与建单金额口径一致）。"""
    return bool(m.random_enable and m.random_min > 0 and m.random_max > 0)


async def load_paypage_merchant(session: SessionDep, cache: CacheDep,
                                token: str) -> tuple[Merchant | None, str]:
    """收款页门禁（web.py /p/{code} 渲染分流用）。

    返回 (merchant, errmsg)：
    - merchant 非空 → 随机金额直跳模式（不渲染表单，服务端建单直跳）
    - merchant 空且 errmsg 非空 → 不可用，渲染错误页
    - 双空 → 普通模式，渲染输金额表单页
    """
    enable = await get_setting(session, "paypage_enable", True, cache=cache)
    if not enable:
        return None, "收款页未开放"
    pt = await paytoken.resolve(session, token)
    if not pt:
        return None, "链接无效"
    m = await _load_merchant(session, pt.merchant_id)
    if not m:
        return None, "商户不存在"
    if m.pay_status != 1:
        return None, "商户暂不可收款"
    if not _random_ready(m):
        return None, ""
    return m, ""


@router.get("/info")
async def paypage_info(request: Request, session: SessionDep, cache: CacheDep,
                       token: str = "", _rl=rate_limit(times=60)):
    """收款页信息：商户名 + 随机金额模式 + 支付方式（token 为 /p/ 短码）。"""
    enable = await get_setting(session, "paypage_enable", True, cache=cache)
    if not enable:
        return err(404, "收款页未开放")
    pt = await paytoken.resolve(session, token)
    if not pt:
        return err(404, "链接无效")
    m = await _load_merchant(session, pt.merchant_id)
    if not m:
        return err(404, "商户不存在")
    return suc_data({
        "pid": m.pid,
        "name": m.username,
        "random_enable": bool(m.random_enable),
        "random_min": str(m.random_min),
        "random_max": str(m.random_max),
    })


class PaypageOrderRequest(BaseModel):
    token: str = ""       # /p/{code} 收款页短码（商户/代理归属在映射行内）
    type: str = ""        # 支付方式 code（空=由收银台选择）
    money: str = ""       # 普通模式金额
    remark: str = ""      # 备注subject


@router.post("/order")
async def create_paypage_order(body: PaypageOrderRequest, request: Request,
                               session: SessionDep, cache: CacheDep,
                               _rl=rate_limit(times=20)):
    """建收款单并拉起支付。随机金额模式由服务端生成金额。"""
    code, message, data = await create_paypage_order_core(body, request, session, cache)
    if code != 0:
        return err(code, message)
    return suc_data(data)


async def create_paypage_order_core(body: PaypageOrderRequest, request: Request,
                                    session: SessionDep, cache: CacheDep,
                                    ) -> tuple[int, str, dict | None]:
    """收款页建单核心（POST /paypage/order 与 GET /p/{code}?go=1 服务端直跳共用）。

    真实拉起支付（选通道 → 插件请求上游）；上游失败时订单保持待支付，
    仍返回 code=0 + cashier_url + pull_failed（收银台链接可重试拉起）。
    返回 (code, message, data)，code==0 表示建单成功。
    """
    enable = await get_setting(session, "paypage_enable", True, cache=cache)
    if not enable:
        return 404, "收款页未开放", None
    pt = await paytoken.resolve(session, body.token)
    if not pt:
        return 404, "链接无效", None
    m = await _load_merchant(session, pt.merchant_id)
    if not m:
        return 404, "商户不存在", None
    if m.pay_status != 1:
        return -1, "商户暂不可收款", None

    client_ip = request.client.host if request.client else ""
    # 代理归属（仅接受该商户名下启用中的代理，停用/删除静默归 0；
    # 归属编码在短码映射行内，外部不可见）
    agent_id = 0
    if pt.agent_id > 0:
        agent = (await session.exec(
            select(Agent).where(
                col(Agent.id) == pt.agent_id,
                col(Agent.merchant_id) == m.id,
                col(Agent.status) == 1,
            )
        )).first()
        agent_id = agent.id if agent else 0

    # 金额：随机模式服务端生成；普通模式取用户输入
    if _random_ready(m):
        money = dmoney(random_money(m))
    else:
        if not body.money:
            return -1, "请输入付款金额", None
        try:
            raw = Decimal(str(body.money).strip())
        except InvalidOperation:
            return -1, "金额格式错误", None
        # NaN/Infinity 能被 Decimal 解析（不是异常），但后续比较/量化会抛
        # InvalidOperation → 500。这里显式挡掉，统一按格式错误处理。
        if not raw.is_finite():
            return -1, "金额格式错误", None
        if raw <= 0:
            return -1, "金额必须大于0", None
        if raw != raw.quantize(Decimal("0.01")):
            return -1, "金额最多两位小数", None
        money = dmoney(raw)

    out_trade_no = "PP" + datetime.now().strftime("%y%m%d%H%M%S%f")[-14:] + str(random.randint(100, 999))
    subject = (body.remark or "在线收款").strip()[:64] or "在线收款"
    referer = request.headers.get("referer", "")
    request_host = urlparse(referer).hostname or "" if referer else ""

    # 支付方式：未指定时取第一个启用项（收银台内可再自选，与 API 下单口径一致）
    type_code = (body.type or "").strip()
    if not type_code:
        first = (await session.exec(
            select(PaymentType).where(PaymentType.status == 1)
            .order_by(col(PaymentType.sort), col(PaymentType.id))
        )).first()
        if not first:
            return -1, "当前支付方式无法使用", None
        type_code = first.code

    try:
        order = await create_order(
            {
                "pid": m.pid,
                "out_trade_no": out_trade_no,
                "name": subject,
                "money": str(money),
                "type": type_code,
                "notify_url": "",
                "return_url": "",
                "client_ip": client_ip,
            },
            session, cache,
            agent_id=agent_id,
            request_host=request_host,
        )
    except ValueError as e:
        return -1, str(e), None
    except InvalidOperation:
        # create_order 内部 Decimal 运算抛出的非法数字（NaN/Infinity 等）
        return -1, "金额格式错误", None

    if order.status != 0:
        return -1, "订单已支付，请勿重复提交", None

    settings = await get_all_settings(session, cache)
    site = (settings.get("site_url") or str(request.base_url)).rstrip("/")
    flow = await process_payment(order, m, session, cache,
                                 is_mobile=True, site_url=site,
                                 notify_base=site, return_base=site)
    if not flow.ok:
        return 0, "", {"trade_no": order.trade_no,
                       "cashier_url": build_cashier_url(site, order.trade_no),
                       "pull_failed": flow.error or "拉起支付失败，可在收银台重试"}
    return 0, "", {
        "trade_no": order.trade_no,
        "money": str(order.money),
        "pay_type": flow.pay_type,
        "pay_info": flow.pay_info,
        "cashier_url": build_cashier_url(site, order.trade_no),
    }
