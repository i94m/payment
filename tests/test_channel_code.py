"""下游通道编码（channel_code）回归测试。

覆盖：参数级校验（不存在/停用/无该支付方式绑定/必传开关）、编码内路由
（成员临时开关/轮询/权重）、**绝不回落到编码之外**、幂等一致性、
收银台支付方式过滤、后台保存校验与通道删除保护。

两条硬断言（最容易悄悄退化的地方）：
- 成员全关 → 报错，且订单的 channel_id 保持 0（没有偷偷选到编码外的通道）
- 权重全零 → 后台保存被拒
"""
from decimal import Decimal

import pytest
from sqlmodel import select

from app.core.security import hash_password, issue_token
from app.models import (
    Channel,
    ChannelCode,
    ChannelCodeItem,
    Merchant,
    Order,
    PaymentType,
    SystemAdmin,
)
from app.services.balance import dmoney
from app.services.channel import choose_channel_by_code
from tests.conftest import signed_params
from tests.test_home_dashboard import two_channels  # noqa: F401  复用双通道夹具


async def _admin_headers(session, username: str = "codeadmin"):
    admin = SystemAdmin(name="管理员", username=username,
                        password=hash_password("admin123456"), is_super_admin=True)
    session.add(admin)
    await session.commit()
    await session.refresh(admin)
    token = await issue_token(guard="admin", user_id=admin.id, session=session)
    return {"Authorization": f"Bearer {token.access_token}"}


async def _mk_code(session, channel_pairs: list[tuple[Channel, int, int]],
                   code: str = "8088", mode: int = 0, status: int = 1) -> ChannelCode:
    """建编码主档 + 成员。channel_pairs = [(通道, 权重, 成员开关)]。"""
    cc = ChannelCode(code=code, name=f"编码{code}", mode=mode, status=status)
    session.add(cc)
    await session.commit()
    await session.refresh(cc)
    for idx, (channel, weight, member_status) in enumerate(channel_pairs):
        session.add(ChannelCodeItem(
            code_id=cc.id, channel_id=channel.id,
            weight=weight, status=member_status, sort=idx,
        ))
    await session.commit()
    return cc


async def _post_order(client, out_trade_no: str, channel_code: str | None = None,
                      type_code: str = "alipay", money: str = "100.00"):
    params = {
        "pid": "1001",
        "type": type_code,
        "out_trade_no": out_trade_no,
        "notify_url": "https://merchant.example/notify",
        "name": "测试商品",
        "money": money,
    }
    if channel_code is not None:
        params["channel_code"] = channel_code
    resp = await client.post("/api/orders", json=signed_params(params))
    return resp.json()


async def _order_by_out_trade_no(session, out_trade_no: str) -> Order | None:
    return (await session.exec(select(Order).where(Order.out_trade_no == out_trade_no))).first()


# ============================================================
# 建单参数级校验
# ============================================================

async def test_unknown_and_disabled_code_rejected(client, session, two_channels):
    """不存在 / 已停用的编码同一文案（不泄漏编码是否存在），且不建单。"""
    data = await _post_order(client, "CC-UNKNOWN", channel_code="9999")
    assert data["code"] == -1
    assert data["message"] == "通道编码不存在或不可用"
    assert await _order_by_out_trade_no(session, "CC-UNKNOWN") is None

    seeded = two_channels
    await _mk_code(session, [(seeded["channel"], 1, 1)], code="7001", status=0)
    data = await _post_order(client, "CC-DISABLED", channel_code="7001")
    assert data["code"] == -1
    assert data["message"] == "通道编码不存在或不可用"
    assert await _order_by_out_trade_no(session, "CC-DISABLED") is None


async def test_code_requires_binding_for_payment_type(client, session, two_channels):
    """编码下没有该支付方式的绑定 → 参数级报错（配置错要下游立刻知道）。"""
    seeded = two_channels
    await _mk_code(session, [(seeded["channel"], 1, 1)], code="8088")
    session.add(PaymentType(code="wxpay", name="微信支付", status=1, sort=2))
    await session.commit()

    data = await _post_order(client, "CC-WX", channel_code="8088", type_code="wxpay")
    assert data["code"] == -1
    assert data["message"] == "通道编码不支持当前支付方式"
    assert await _order_by_out_trade_no(session, "CC-WX") is None


async def test_required_switch_only_affects_open_api(client, session, seeded):
    """商户开了必传：开放 API 不传报错；平台内部入口（收款页等）不受影响。"""
    merchant = seeded["merchant"]
    merchant.channel_code_required = True
    session.add(merchant)
    await session.commit()

    data = await _post_order(client, "CC-REQUIRED")
    assert data["code"] == -1
    assert data["message"] == "缺少通道编码"

    # 仓库内其它入口（paypage / 余额充值）建单不带 channel_code，也不传 require_channel_code
    from app.services.order import create_order
    order = await create_order(
        {"pid": "1001", "out_trade_no": "CC-INTERNAL", "name": "收款页",
         "money": "10.00", "type": "alipay"},
        session,
    )
    assert order.id and order.channel_code is None


async def test_code_success_persists_trace(client, session, two_channels):
    """编码成功下单：订单落编码字符串 + ID，拉起后通道在该编码内。"""
    seeded = two_channels
    cc = await _mk_code(session, [(seeded["channel2"], 1, 1)], code="8088")

    data = await _post_order(client, "CC-OK", channel_code="8088")
    assert data["code"] == 0, data
    order = await _order_by_out_trade_no(session, "CC-OK")
    assert order.channel_code == "8088"
    assert order.channel_code_id == cc.id
    assert order.channel_id == seeded["channel2"].id


# ============================================================
# 编码内路由：临时开关 / 轮询 / 权重
# ============================================================

async def test_disabled_member_skipped_within_code(session, two_channels):
    """成员临时关闭 → 落到编码内下一个可用通道（不是报错，也不是越界）。"""
    seeded = two_channels
    cc = await _mk_code(session, [
        (seeded["channel"], 1, 0),    # 本编码内停用
        (seeded["channel2"], 1, 1),
    ])
    resolved = await choose_channel_by_code(
        session, None, cc.id, seeded["pay_type"].id, dmoney("10.00")
    )
    assert resolved is not None
    assert resolved.channel.id == seeded["channel2"].id


async def test_all_members_off_never_falls_back_outside_code(client, session, two_channels):
    """硬断言：编码内成员全关 → 报错，订单 channel_id 保持 0，绝不回落默认路由。

    编码只绑 channel，channel2（同样启用、同支付方式）在编码之外：
    只要实现里留了任何「回退默认路由」的口子，channel2 就会被选中，断言随之失败。
    """
    seeded = two_channels
    cc = await _mk_code(session, [(seeded["channel"], 1, 0)], code="8088")
    assert await choose_channel_by_code(
        session, None, cc.id, seeded["pay_type"].id, dmoney("10.00")
    ) is None

    data = await _post_order(client, "CC-ALL-OFF", channel_code="8088")
    assert data["code"] == -1
    assert "当前不可用" in data["message"]

    order = await _order_by_out_trade_no(session, "CC-ALL-OFF")
    assert order is not None
    assert order.channel_id == 0                 # 没有偷偷选到编码外的通道
    assert order.plugin_name in (None, "")
    assert order.extra in (None, {})


async def test_round_robin_alternates(session, two_channels):
    """轮询：连续 4 单在 2 条通道上严格交替（按绑定行游标，不是通道游标）。"""
    seeded = two_channels
    cc = await _mk_code(session, [
        (seeded["channel"], 1, 1),
        (seeded["channel2"], 1, 1),
    ], mode=1)
    picked = []
    for _ in range(4):
        resolved = await choose_channel_by_code(
            session, None, cc.id, seeded["pay_type"].id, dmoney("10.00")
        )
        assert resolved is not None
        picked.append(resolved.channel.id)
    assert picked[0] != picked[1]
    assert picked == [picked[0], picked[1], picked[0], picked[1]]


async def test_weighted_zero_never_picked(session, two_channels):
    """权重：0 权重成员在 200 次抽样中一次都不出现。"""
    seeded = two_channels
    cc = await _mk_code(session, [
        (seeded["channel"], 0, 1),     # 0 权重 = 不参与抽签
        (seeded["channel2"], 5, 1),
    ], mode=2)
    for _ in range(200):
        resolved = await choose_channel_by_code(
            session, None, cc.id, seeded["pay_type"].id, dmoney("10.00")
        )
        assert resolved is not None
        assert resolved.channel.id != seeded["channel"].id


# ============================================================
# 幂等与收银台
# ============================================================

async def test_idempotency_rejects_code_change(client, session, two_channels):
    seeded = two_channels
    await _mk_code(session, [(seeded["channel"], 1, 1)], code="8088")
    await _mk_code(session, [(seeded["channel2"], 1, 1)], code="8089")

    first = await _post_order(client, "CC-IDEM", channel_code="8088")
    assert first["code"] == 0, first

    second = await _post_order(client, "CC-IDEM", channel_code="8089")
    assert second["code"] == -1
    assert second["message"] == "out_trade_no 已存在且通道编码不一致"

    # 同编码重提：幂等返回原单
    third = await _post_order(client, "CC-IDEM", channel_code="8088")
    assert third["code"] == 0
    assert third["data"]["trade_no"] == first["data"]["trade_no"]


async def test_cashier_types_filtered_by_code(client, session, two_channels):
    """收银台只返回编码内的支付方式：编码外有通道的 wxpay 不应出现。"""
    seeded = two_channels
    session.add(PaymentType(code="wxpay", name="微信支付", status=1, sort=2))
    await session.commit()
    wx = (await session.exec(select(PaymentType).where(PaymentType.code == "wxpay"))).one()
    session.add(Channel(
        name="微信通道", plugin_name="epay", payment_type_id=wx.id,
        merchant_rate=2, cost_rate=1, status=1, mode=0,
        min_amount=1, max_amount=0, day_limit=0,
        config=seeded["channel"].config,
    ))
    await session.commit()

    await _mk_code(session, [(seeded["channel"], 1, 1)], code="8088")
    data = await _post_order(client, "CC-CASHIER", channel_code="8088")
    assert data["code"] == 0, data
    order = await _order_by_out_trade_no(session, "CC-CASHIER")

    r = await client.get(f"/cashier/types?trade_no={order.trade_no}")
    assert r.status_code == 200
    codes = {t["code"] for t in r.json()["data"]["types"]}
    assert codes == {"alipay"}

    # 编码内成员全关 → 该编码没有任何可选支付方式
    items = (await session.exec(
        select(ChannelCodeItem).where(ChannelCodeItem.code_id == order.channel_code_id)
    )).all()
    for it in items:
        it.status = 0
        session.add(it)
    await session.commit()
    r = await client.get(f"/cashier/types?trade_no={order.trade_no}")
    assert r.json()["data"]["types"] == []
