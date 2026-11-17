"""商户端/代理端展示与口径修复回归（缺陷编号对照玲的清单）：

- B01 商户端订单详情字段错位（name/addtime/endtime → subject/created_at/paid_at）、
  已退款单状态仍显示「已支付」，且看不到退款信息
- B02 已退款（status=2）在列表/筛选/导出里的映射
- B09 商户端资金明细业务类型、站内消息 biz_type 显示英文 code
- B12 商户端订单汇总把未支付/已退款计入
- B13 0 单新商户「近 7 日成功率」返回 100%
- B14 订单「支付方式」显示英文 code（商户端/代理端）
"""
from decimal import Decimal

import pytest
from sqlmodel import select

from app.models import Merchant, MerchantRecord, Order
from app.services.message import send_merchant_message
from tests.test_agent_panel import _mk_agent, _mk_order
from tests.test_refund import _pay_order, _refund


def _m(v) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.01"))


async def _get_merchant(session) -> Merchant:
    return (await session.exec(select(Merchant).where(Merchant.pid == 1001))).one()


async def _mch_headers(session) -> dict:
    m = await _get_merchant(session)
    from app.core.security import issue_token
    token = await issue_token(guard="merchant", user_id=m.id, session=session)
    return {"Authorization": f"Bearer {token.access_token}"}


async def _agent_headers(session, agent_id: int) -> dict:
    from app.core.security import issue_token
    token = await issue_token(guard="agent", user_id=agent_id, session=session)
    return {"Authorization": f"Bearer {token.access_token}"}


# ------------------------------------------------------------
# B01 商户端订单详情
# ------------------------------------------------------------

@pytest.mark.asyncio
async def test_merchant_order_detail_uses_panel_fields(client, seeded, session):
    """详情与列表同形：subject/created_at/paid_at 有值（不再恒为 —）。"""
    headers = await _mch_headers(session)
    order = await _pay_order(client, session, "DTL1")

    body = (await client.get(f"/merchant-api/order/{order.trade_no}", headers=headers)).json()
    assert body["code"] == 0, body
    data = body["data"]
    assert data["subject"] == "退款测试"
    assert data["created_at"]  # 不再是空串
    assert data["paid_at"]
    assert data["status"] == 1
    assert data["refund_status"] == "NONE"
    assert data["refunded_amount"] == "0.00"


@pytest.mark.asyncio
async def test_merchant_order_detail_shows_refund(client, seeded, session):
    """全额退款后详情是「已退款」（status=2）+ 退款金额，不再谎报已支付。"""
    headers = await _mch_headers(session)
    order = await _pay_order(client, session, "DTL2")
    body = await _refund(client, order.trade_no)
    assert body["code"] == 0, body

    data = (await client.get(f"/merchant-api/order/{order.trade_no}", headers=headers)).json()["data"]
    assert data["status"] == 2
    assert data["refund_status"] == "FULL"
    assert _m(data["refunded_amount"]) == _m("98.00")


@pytest.mark.asyncio
async def test_merchant_order_detail_deducted_stays_unpaid(client, seeded, session):
    """扣量单商户视角仍是未支付、无完成时间、无退款信息。"""
    headers = await _mch_headers(session)
    m = await _get_merchant(session)
    o = await _mk_order(session, m.id, status=1, deducted=True)
    o.is_deducted = True
    session.add(o)
    await session.commit()

    data = (await client.get(f"/merchant-api/order/{o.trade_no}", headers=headers)).json()["data"]
    assert data["status"] == 0
    assert data["paid_at"] == ""
    assert data["refund_status"] == "NONE"


# ------------------------------------------------------------
# B02 已退款状态可筛选
# ------------------------------------------------------------

@pytest.mark.asyncio
async def test_merchant_refunded_order_filterable(client, seeded, session):
    headers = await _mch_headers(session)
    order = await _pay_order(client, session, "ST2")
    assert (await _refund(client, order.trade_no))["code"] == 0

    data = (await client.post("/merchant-api/order/list", json={"status": 2},
                              headers=headers)).json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["trade_no"] == order.trade_no
    assert data["items"][0]["status"] == 2


@pytest.mark.asyncio
async def test_merchant_export_uses_chinese_text(client, seeded, session):
    """CSV 导出：状态/支付方式给中文，不再输出裸 code。"""
    headers = await _mch_headers(session)
    await _pay_order(client, session, "EXP2")
    text = (await client.post("/merchant-api/order/export", json={},
                              headers=headers)).content.decode("utf-8-sig")
    assert "支付宝" in text
    assert "已支付" in text


# ------------------------------------------------------------
# B09 资金明细 / 站内消息业务类型
# ------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_and_message_biz_type_name(client, seeded, session):
    headers = await _mch_headers(session)
    await _pay_order(client, session, "BIZ1")
    # 下单/回调会 expire_all，商户实例要在其之后取
    m = await _get_merchant(session)

    records = (await client.get("/merchant-api/record/list", headers=headers)).json()["data"]
    income = [r for r in records["items"] if r["biz_type"] == "order_income"]
    assert income and income[0]["biz_type_name"] == "订单收入"

    await send_merchant_message(session, m, "标题", "内容")
    await session.commit()
    msgs = (await client.get("/merchant-api/message/list", headers=headers)).json()["data"]
    assert msgs["items"][0]["biz_type"] == "system"
    assert msgs["items"][0]["biz_type_name"] == "系统通知"


# ------------------------------------------------------------
# B12 订单汇总口径
# ------------------------------------------------------------

@pytest.mark.asyncio
async def test_order_summary_excludes_unpaid_and_refunded(client, seeded, session):
    headers = await _mch_headers(session)
    order = await _pay_order(client, session, "SUM2")
    # 再挂一笔未支付单，不应计入默认汇总
    await _mk_order(session, (await _get_merchant(session)).id, status=0, paid=None)

    summary = (await client.post("/merchant-api/order/list", json={},
                                 headers=headers)).json()["data"]["summary"]
    assert summary["count"] == 1
    assert _m(summary["money"]) == _m("100.00")
    assert _m(summary["income"]) == _m("98.00")

    # 全额退款后默认汇总归零（余额已冲回，不能再显示到账）
    assert (await _refund(client, order.trade_no))["code"] == 0
    summary = (await client.post("/merchant-api/order/list", json={},
                                 headers=headers)).json()["data"]["summary"]
    assert summary["count"] == 0
    assert _m(summary["money"]) == _m("0")
    assert _m(summary["income"]) == _m("0")

    # 显式筛「已退款」时按所选状态统计，便于对账
    summary = (await client.post("/merchant-api/order/list", json={"status": 2},
                                 headers=headers)).json()["data"]["summary"]
    assert summary["count"] == 1
    assert _m(summary["money"]) == _m("100.00")


# ------------------------------------------------------------
# B13 0 单成功率
# ------------------------------------------------------------

@pytest.mark.asyncio
async def test_dashboard_success_rate_null_when_no_orders(client, seeded, session):
    headers = await _mch_headers(session)
    data = (await client.get("/merchant-api/dashboard", headers=headers)).json()["data"]
    assert data["success_rate"] is None


# ------------------------------------------------------------
# B14 支付方式显示名
# ------------------------------------------------------------

@pytest.mark.asyncio
async def test_payment_type_name_in_merchant_and_agent_lists(client, seeded, session):
    mch_headers = await _mch_headers(session)
    paid = await _pay_order(client, session, "TYPE1")
    m = await _get_merchant(session)

    items = (await client.post("/merchant-api/order/list", json={},
                               headers=mch_headers)).json()["data"]["items"]
    item = [i for i in items if i["trade_no"] == paid.trade_no][0]
    assert item["type"] == "alipay"
    assert item["type_name"] == "支付宝"

    agent = await _mk_agent(session, m.id, "agent-type")
    await _mk_order(session, m.id, agent_id=agent.id)
    agent_headers = await _agent_headers(session, agent.id)
    rows = (await client.get("/agent-api/order/list", headers=agent_headers)).json()["data"]["items"]
    assert rows[0]["type"] == "alipay"
    assert rows[0]["type_name"] == "支付宝"


@pytest.mark.asyncio
async def test_payment_type_name_falls_back_to_code(client, seeded, session):
    """payment_types 里没有的 code（历史单/已删支付方式）回退原 code，不报错。"""
    from app.services.order import payment_type_name_map
    names = await payment_type_name_map(session)
    assert names.get("alipay") == "支付宝"
    assert names.get("nonexistent", "nonexistent") == "nonexistent"
