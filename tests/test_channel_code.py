"""通道编码（channel_code）回归测试。

覆盖：参数级校验（不存在/停用/无该支付方式绑定/必传开关）、编码内路由
（全局停用跳过/轮询/权重）、**绝不回落到编码之外**、幂等一致性、
收银台支付方式过滤、后台保存校验与通道删除保护。

两条硬断言（最容易悄悄退化的地方）：
- 编码内通道全不可用 → 报错，且订单的 channel_id 保持 0（没有偷偷选到编码外的通道）
- 权重全零 → 后台保存被拒；已存在的全零配置不选通道（不退化成随机）
"""
import random
from decimal import Decimal

import pytest
from sqlmodel import select

from app.core.security import hash_password, issue_token
from app.models import (
    Channel,
    ChannelCode,
    ChannelCodeItem,
    Order,
    PaymentType,
    SystemAdmin,
    SystemAdminPermission,
    SystemPermission,
)
from app.services.balance import dmoney
from app.services.channel import _order_code_candidates, choose_channel_by_code
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


async def _scoped_admin_headers(session, username: str, perms: tuple[str, ...]):
    """非超管账号：只授予指定权限键（验证通道编码的独立权限）。"""
    admin = SystemAdmin(name="管理员", username=username,
                        password=hash_password("admin123456"), is_super_admin=False)
    session.add(admin)
    await session.commit()
    await session.refresh(admin)
    for key in perms:
        perm = (await session.exec(
            select(SystemPermission).where(SystemPermission.key == key)
        )).first()
        if perm is None:
            perm = SystemPermission(name=key, key=key)
            session.add(perm)
            await session.commit()
            await session.refresh(perm)
        session.add(SystemAdminPermission(admin_id=admin.id, permission_id=perm.id))
    await session.commit()
    token = await issue_token(guard="admin", user_id=admin.id, session=session)
    return {"Authorization": f"Bearer {token.access_token}"}


async def _mk_code(session, channel_pairs: list[tuple[Channel, int]],
                   code: str = "8088", mode: int = 0, status: int = 1) -> ChannelCode:
    """建编码主档 + 成员。channel_pairs = [(通道, 权重)]；勾选即启用，没有二级开关。"""
    cc = ChannelCode(code=code, name=f"编码{code}", mode=mode, status=status)
    session.add(cc)
    await session.commit()
    await session.refresh(cc)
    for idx, (channel, weight) in enumerate(channel_pairs):
        session.add(ChannelCodeItem(
            code_id=cc.id, channel_id=channel.id, weight=weight, sort=idx,
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
    await _mk_code(session, [(seeded["channel"], 1)], code="7001", status=0)
    data = await _post_order(client, "CC-DISABLED", channel_code="7001")
    assert data["code"] == -1
    assert data["message"] == "通道编码不存在或不可用"
    assert await _order_by_out_trade_no(session, "CC-DISABLED") is None


async def test_code_requires_binding_for_payment_type(client, session, two_channels):
    """编码下没有该支付方式的绑定 → 参数级报错（配置错要下游立刻知道）。"""
    seeded = two_channels
    await _mk_code(session, [(seeded["channel"], 1)], code="8088")
    session.add(PaymentType(code="wxpay", name="微信支付", status=1, sort=2))
    await session.commit()

    data = await _post_order(client, "CC-WX", channel_code="8088", type_code="wxpay")
    assert data["code"] == -1
    assert data["message"] == "通道编码不支持当前支付方式"
    assert await _order_by_out_trade_no(session, "CC-WX") is None


async def test_channel_code_is_optional(client, session, seeded):
    """编码必传已去掉：开放 API 不带 channel_code 也能建单，按默认路由分流。"""
    data = await _post_order(client, "CC-OPTIONAL")
    assert data["code"] == 0, data
    order = await _order_by_out_trade_no(session, "CC-OPTIONAL")
    assert order.channel_code is None
    assert order.channel_code_id == 0

    # 平台内部入口（paypage / 余额充值）本就不传编码，行为保持不变
    from app.services.order import create_order
    internal = await create_order(
        {"pid": "1001", "out_trade_no": "CC-INTERNAL", "name": "收款页",
         "money": "10.00", "type": "alipay"},
        session,
    )
    assert internal.id and internal.channel_code is None


async def test_code_success_persists_trace(client, session, two_channels):
    """编码成功下单：订单落编码字符串 + ID，拉起后通道在该编码内。"""
    seeded = two_channels
    cc = await _mk_code(session, [(seeded["channel2"], 1)], code="8088")

    data = await _post_order(client, "CC-OK", channel_code="8088")
    assert data["code"] == 0, data
    order = await _order_by_out_trade_no(session, "CC-OK")
    assert order.channel_code == "8088"
    assert order.channel_code_id == cc.id
    assert order.channel_id == seeded["channel2"].id


# ============================================================
# 编码内路由：全局停用跳过 / 轮询 / 权重
# ============================================================

async def test_globally_disabled_channel_skipped_within_code(session, two_channels):
    """编码内某条通道全局停用 → 落到编码内下一个可用通道（不是报错，也不是越界）。"""
    seeded = two_channels
    seeded["channel"].status = 0
    session.add(seeded["channel"])
    await session.commit()
    cc = await _mk_code(session, [
        (seeded["channel"], 1),    # 全局停用
        (seeded["channel2"], 1),
    ])
    resolved = await choose_channel_by_code(
        session, None, cc.id, seeded["pay_type"].id, dmoney("10.00")
    )
    assert resolved is not None
    assert resolved.channel.id == seeded["channel2"].id


async def test_code_without_available_channel_never_falls_back(client, session, two_channels):
    """硬断言：编码内通道全部不可用 → 报错，订单 channel_id 保持 0，绝不回落默认路由。

    编码只绑 channel（全局停用），channel2（同样启用、同支付方式）在编码之外：
    只要实现里留了任何「回退默认路由」的口子，channel2 就会被选中，断言随之失败。
    """
    seeded = two_channels
    seeded["channel"].status = 0
    session.add(seeded["channel"])
    await session.commit()
    cc = await _mk_code(session, [(seeded["channel"], 1)], code="8088")
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
        (seeded["channel"], 1),
        (seeded["channel2"], 1),
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
        (seeded["channel"], 0),     # 0 权重 = 不参与抽签
        (seeded["channel2"], 100),
    ], mode=2)
    for _ in range(200):
        resolved = await choose_channel_by_code(
            session, None, cc.id, seeded["pay_type"].id, dmoney("10.00")
        )
        assert resolved is not None
        assert resolved.channel.id != seeded["channel"].id


async def test_weighted_zero_excluded_from_candidates(session, two_channels):
    """权重 0 不只是「不容易选中」：候选列表里根本没有它，限速回退也轮不到。"""
    seeded = two_channels
    channel, channel2 = seeded["channel"], seeded["channel2"]
    item_map = {
        channel.id: ChannelCodeItem(code_id=1, channel_id=channel.id, weight=0),
        channel2.id: ChannelCodeItem(code_id=1, channel_id=channel2.id, weight=100),
    }
    for _ in range(50):
        picked = _order_code_candidates([channel, channel2], 2, item_map)
        assert [c.id for c in picked] == [channel2.id]


async def test_weighted_all_zero_code_has_no_candidate(session, two_channels):
    """防御：绕过后台校验写进全零权重时，编码内无候选（报不可用），不退化成随机、不越界。"""
    seeded = two_channels
    cc = await _mk_code(session, [
        (seeded["channel"], 0),
        (seeded["channel2"], 0),
    ], mode=2)
    assert await choose_channel_by_code(
        session, None, cc.id, seeded["pay_type"].id, dmoney("10.00")
    ) is None


async def test_weighted_distribution_follows_ratio(session, two_channels):
    """权重：25 / 75 两条通道的实际命中比例落在 1/4 附近（不是等概率随机）。"""
    seeded = two_channels
    channel, channel2 = seeded["channel"], seeded["channel2"]
    cc = await _mk_code(session, [(channel, 25), (channel2, 75)], mode=2)
    rounds = 400
    hits = {channel.id: 0, channel2.id: 0}
    for _ in range(rounds):
        resolved = await choose_channel_by_code(
            session, None, cc.id, seeded["pay_type"].id, dmoney("10.00")
        )
        assert resolved is not None
        hits[resolved.channel.id] += 1
    share = hits[channel.id] / rounds
    assert 0.15 < share < 0.35, hits      # 期望 0.25，留足随机波动


async def test_weighted_three_channels_50_30_20(session, two_channels):
    """用户场景：一个编码 3 个通道，权重 50 / 30 / 20（合计 100），命中比例就是 50/30/20。

    走完整链路（`choose_channel_by_code`）而不是只测抽签函数：金额过滤、限速占坑等前置
    环节都不能把分布带偏。
    """
    seeded = two_channels
    channel, channel2 = seeded["channel"], seeded["channel2"]
    channel3 = Channel(
        name="通道三", plugin_name="epay", payment_type_id=seeded["pay_type"].id,
        merchant_rate=2, cost_rate=1, status=1, mode=0,
        min_amount=1, max_amount=0, day_limit=0,
        config=channel.config,
    )
    session.add(channel3)
    await session.commit()
    await session.refresh(channel3)

    cc = await _mk_code(session, [(channel, 50), (channel2, 30), (channel3, 20)], mode=2)
    rounds = 600
    hits = {channel.id: 0, channel2.id: 0, channel3.id: 0}
    state = random.getstate()
    random.seed(20261121)
    try:
        for _ in range(rounds):
            resolved = await choose_channel_by_code(
                session, None, cc.id, seeded["pay_type"].id, dmoney("10.00")
            )
            assert resolved is not None
            hits[resolved.channel.id] += 1
    finally:
        random.setstate(state)
    assert 0.42 < hits[channel.id] / rounds < 0.58, hits     # 期望 0.50
    assert 0.23 < hits[channel2.id] / rounds < 0.37, hits    # 期望 0.30
    assert 0.13 < hits[channel3.id] / rounds < 0.27, hits    # 期望 0.20


# ============================================================
# 幂等与收银台
# ============================================================

async def test_idempotency_rejects_code_change(client, session, two_channels):
    seeded = two_channels
    await _mk_code(session, [(seeded["channel"], 1)], code="8088")
    await _mk_code(session, [(seeded["channel2"], 1)], code="8089")

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

    await _mk_code(session, [(seeded["channel"], 1)], code="8088")
    data = await _post_order(client, "CC-CASHIER", channel_code="8088")
    assert data["code"] == 0, data
    order = await _order_by_out_trade_no(session, "CC-CASHIER")

    r = await client.get(f"/cashier/types?trade_no={order.trade_no}")
    assert r.status_code == 200
    codes = {t["code"] for t in r.json()["data"]["types"]}
    assert codes == {"alipay"}

    # 编码内通道全局停用 → 该编码没有任何可选支付方式
    channel = await session.get(Channel, seeded["channel"].id)
    channel.status = 0
    session.add(channel)
    await session.commit()
    r = await client.get(f"/cashier/types?trade_no={order.trade_no}")
    assert r.json()["data"]["types"] == []


async def test_cashier_reports_missing_or_disabled_code(client, session, two_channels):
    """编码被删/停用后，收银台报「编码不存在或不可用」，不能误报成「不支持该支付方式」。"""
    seeded = two_channels
    cc = await _mk_code(session, [(seeded["channel"], 1)], code="8088")
    data = await _post_order(client, "CC-GONE", channel_code="8088")
    assert data["code"] == 0, data
    order = await _order_by_out_trade_no(session, "CC-GONE")

    # 停用：收银台两种入口都按编码不可用报错（不静默返回空列表）
    cc.status = 0
    session.add(cc)
    await session.commit()
    r = await client.get(f"/cashier/types?trade_no={order.trade_no}")
    assert r.json()["code"] == -1
    assert r.json()["message"] == "通道编码不存在或不可用"
    r = await client.post("/cashier/pay", json={"trade_no": order.trade_no, "type": "alipay"})
    assert r.json()["code"] == -1
    assert r.json()["message"] == "通道编码不存在或不可用"

    # 删除（绕过后台的待支付保护，模拟编码已被移走）：文案保持一致
    for it in (await session.exec(
        select(ChannelCodeItem).where(ChannelCodeItem.code_id == cc.id)
    )).all():
        await session.delete(it)
    await session.delete(cc)
    await session.commit()
    r = await client.get(f"/cashier/types?trade_no={order.trade_no}")
    assert r.json()["message"] == "通道编码不存在或不可用"
    r = await client.post("/cashier/pay", json={"trade_no": order.trade_no, "type": "alipay"})
    assert r.json()["message"] == "通道编码不存在或不可用"
# ============================================================
# 后台：保存校验与通道删除保护
# ============================================================

async def test_admin_rejects_all_zero_weights(client, session, two_channels):
    """硬断言：权重模式全零权重 → 保存被拒（否则会退化成永远选最后一条）。"""
    headers = await _admin_headers(session)
    seeded = two_channels
    body = {
        "code": "8088", "name": "编码8088", "remark": "", "mode": 2, "status": 1,
        "items": [
            {"channel_id": seeded["channel"].id, "weight": 0},
            {"channel_id": seeded["channel2"].id, "weight": 0},
        ],
    }
    r = await client.post("/admin-api/pay/channel-codes", json=body, headers=headers)
    assert r.status_code == 400
    assert r.json()["code"] == -1
    assert "权重" in r.json()["message"]
    assert (await session.exec(select(ChannelCode))).first() is None

    # 全零之外，权重必须按百分比配置且合计 100
    body["items"][0]["weight"] = 40
    body["items"][1]["weight"] = 60
    r = await client.post("/admin-api/pay/channel-codes", json=body, headers=headers)
    assert r.json()["code"] == 0, r.text
    assert (await session.exec(select(ChannelCode))).first() is not None


async def test_admin_rejects_weight_total_not_100(client, session, two_channels):
    """硬断言：权重按百分比配置，合计不等于 100 的保存一律被拒（含单条超 100）。"""
    headers = await _admin_headers(session)
    seeded = two_channels
    body = {
        "code": "8088", "name": "编码8088", "remark": "", "mode": 2, "status": 1,
        "items": [
            {"channel_id": seeded["channel"].id, "weight": 30},
            {"channel_id": seeded["channel2"].id, "weight": 30},
        ],
    }
    r = await client.post("/admin-api/pay/channel-codes", json=body, headers=headers)
    assert r.status_code == 400
    assert r.json()["code"] == -1
    assert r.json()["message"] == "权重合计必须等于 100（当前 60）"
    assert (await session.exec(select(ChannelCode))).first() is None

    # 单条超过 100 也要拦（否则「合计 100」可以被 150 + (-50) 之类的脏数据绕过）
    body["items"] = [
        {"channel_id": seeded["channel"].id, "weight": 150},
        {"channel_id": seeded["channel2"].id, "weight": 0},
    ]
    r = await client.post("/admin-api/pay/channel-codes", json=body, headers=headers)
    assert r.json()["code"] == -1
    assert "不能超过 100" in r.json()["message"]

    # 合计恰好 100（允许有 0 权重成员）即可保存
    body["items"] = [
        {"channel_id": seeded["channel"].id, "weight": 100},
        {"channel_id": seeded["channel2"].id, "weight": 0},
    ]
    r = await client.post("/admin-api/pay/channel-codes", json=body, headers=headers)
    assert r.json()["code"] == 0, r.text
    detail = (await client.get(
        f"/admin-api/pay/channel-codes/{r.json()['data']['id']}", headers=headers
    )).json()["data"]
    assert [it["weight"] for it in detail["items"]] == [100, 0]


async def test_admin_channel_delete_blocked_when_referenced(client, session, two_channels):
    headers = await _admin_headers(session)
    seeded = two_channels
    await _mk_code(session, [(seeded["channel"], 1)], code="8088")

    r = await client.delete(f"/admin-api/pay/channels/{seeded['channel'].id}", headers=headers)
    assert r.status_code == 400
    assert r.json()["code"] == -1
    assert "通道编码" in r.json()["message"]
    # 通道还在
    assert await session.get(Channel, seeded["channel"].id) is not None

    # 未被引用的通道照旧可删
    r = await client.delete(f"/admin-api/pay/channels/{seeded['channel2'].id}", headers=headers)
    assert r.json()["code"] == 0


async def test_admin_code_delete_blocked_by_pending_order(client, session, two_channels):
    """编码仍有待支付订单引用时拒绝删除（否则收银台按已删编码选通道会全空/误报）。"""
    headers = await _admin_headers(session)
    seeded = two_channels
    cc = await _mk_code(session, [(seeded["channel"], 1)], code="8088")
    data = await _post_order(client, "CC-DEL-PENDING", channel_code="8088")
    assert data["code"] == 0, data

    r = await client.delete(f"/admin-api/pay/channel-codes/{cc.id}", headers=headers)
    assert r.status_code == 400
    assert r.json()["code"] == -1
    assert "待支付" in r.json()["message"]
    assert await session.get(ChannelCode, cc.id) is not None

    # 待支付订单处理掉之后可删
    order = await _order_by_out_trade_no(session, "CC-DEL-PENDING")
    order.status = 1
    session.add(order)
    await session.commit()
    code_id = cc.id
    r = await client.delete(f"/admin-api/pay/channel-codes/{code_id}", headers=headers)
    assert r.json()["code"] == 0, r.text
    session.expire_all()
    assert await session.get(ChannelCode, code_id) is None


async def test_admin_code_crud_and_status_toggle(client, session, two_channels):
    """编码主档/成员整表覆盖写 + 编码总闸。"""
    headers = await _admin_headers(session)
    seeded = two_channels
    body = {
        "code": "8088", "name": "下游A", "remark": "备注", "mode": 1, "status": 1,
        "items": [{"channel_id": seeded["channel"].id, "weight": 1}],
    }
    r = await client.post("/admin-api/pay/channel-codes", json=body, headers=headers)
    assert r.json()["code"] == 0, r.text
    code_id = r.json()["data"]["id"]

    # 编码统一小写：下游大小写不敏感
    r = await client.post("/admin-api/pay/channel-codes",
                          json={**body, "code": "ABCD"}, headers=headers)
    assert r.json()["data"]["code"] == "abcd"

    # 列表/详情
    r = await client.get("/admin-api/pay/channel-codes", headers=headers)
    assert r.json()["data"]["total"] == 2
    r = await client.get(f"/admin-api/pay/channel-codes/{code_id}", headers=headers)
    detail = r.json()["data"]
    assert [it["channel_id"] for it in detail["items"]] == [seeded["channel"].id]

    # 成员整表覆盖写：换成 channel2
    r = await client.put(f"/admin-api/pay/channel-codes/{code_id}", json={
        **body, "items": [{"channel_id": seeded["channel2"].id, "weight": 2}],
    }, headers=headers)
    assert r.json()["code"] == 0, r.text
    r = await client.get(f"/admin-api/pay/channel-codes/{code_id}", headers=headers)
    assert [it["channel_id"] for it in r.json()["data"]["items"]] == [seeded["channel2"].id]

    # 轻量开关：编码总闸（成员「勾选即启用」，没有二级开关）
    r = await client.put(f"/admin-api/pay/channel-codes/{code_id}/status",
                         json={"status": 0}, headers=headers)
    assert r.json()["data"]["status"] == 0

    # 删除后编码消失，历史订单留痕不受影响
    r = await client.delete(f"/admin-api/pay/channel-codes/{code_id}", headers=headers)
    assert r.json()["code"] == 0
    assert await session.get(ChannelCode, code_id) is None


async def test_admin_code_unique_and_format(client, session, two_channels):
    headers = await _admin_headers(session)
    seeded = two_channels
    body = {
        "code": "8088", "name": "下游A", "remark": "", "mode": 0, "status": 1,
        "items": [{"channel_id": seeded["channel"].id, "weight": 1}],
    }
    assert (await client.post("/admin-api/pay/channel-codes", json=body,
                              headers=headers)).json()["code"] == 0
    dup = await client.post("/admin-api/pay/channel-codes", json=body, headers=headers)
    assert dup.json()["code"] == -1
    assert "已存在" in dup.json()["message"]

    bad = await client.post("/admin-api/pay/channel-codes",
                            json={**body, "code": "有中文"}, headers=headers)
    assert bad.json()["code"] == -1

    empty = await client.post("/admin-api/pay/channel-codes",
                              json={**body, "code": "9001", "items": []}, headers=headers)
    assert empty.json()["code"] == -1
    assert "通道" in empty.json()["message"]


async def test_admin_order_list_channel_code_filter(client, session, two_channels):
    """后台订单列表按编码筛选 + 返回编码字段。"""
    headers = await _admin_headers(session)
    seeded = two_channels
    cc = await _mk_code(session, [(seeded["channel"], 1)], code="8088")
    data = await _post_order(client, "CC-LIST", channel_code="8088")
    assert data["code"] == 0, data

    r = await client.post("/admin-api/order/list",
                          json={"channel_code_id": cc.id}, headers=headers)
    body = r.json()
    assert body["code"] == 0, body
    assert body["data"]["total"] == 1
    assert body["data"]["items"][0]["channel_code"] == "8088"

    r = await client.post("/admin-api/order/list",
                          json={"channel_code_id": 999999}, headers=headers)
    assert r.json()["data"]["total"] == 0


async def test_channel_code_has_own_permissions(client, session, two_channels):
    """通道编码使用独立权限（本页此前复用 channel.*）。

    `channel.view` 不再顺带看到编码页，「查看」也要显式授予 channel_code.view；
    只读账号管不到写操作（创建/删除仍 403）。
    """
    seeded = two_channels
    cc = await _mk_code(session, [(seeded["channel"], 1)], code="8088")

    channel_only = await _scoped_admin_headers(session, "ccchanview", ("channel.view",))
    r = await client.get("/admin-api/pay/channel-codes", headers=channel_only)
    assert r.status_code == 403

    code_view = await _scoped_admin_headers(session, "cccodeview", ("channel_code.view",))
    r = await client.get("/admin-api/pay/channel-codes", headers=code_view)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == 0
    assert body["data"]["total"] == 1
    assert body["data"]["items"][0]["code"] == "8088"

    payload = {
        "code": "9001", "name": "只读账号", "remark": "", "mode": 0, "status": 1,
        "items": [{"channel_id": seeded["channel"].id, "weight": 1, "status": 1, "sort": 0}],
    }
    assert (await client.post("/admin-api/pay/channel-codes", json=payload,
                              headers=code_view)).status_code == 403
    assert (await client.delete(f"/admin-api/pay/channel-codes/{cc.id}",
                                headers=code_view)).status_code == 403


async def test_code_page_dictionaries_need_only_their_own_permission(client, session, two_channels):
    """编码页用到的字典接口不能被别模块的权限挡住（403 会被前端全局跳到错误页）。

    只授予 channel_code.view 的账号：通道候选（channel-options）与支付方式名称（types）
    必须可读，否则进页面就 403；但通道本体（含费率/凭证）仍要 channel.view。
    反之只授予 channel.view 的账号，也要能读到支付方式字典（通道页同样要用）。
    """
    seeded = two_channels
    code_only = await _scoped_admin_headers(session, "ccdict", ("channel_code.view",))

    r = await client.get("/admin-api/pay/channel-options", headers=code_only)
    assert r.status_code == 200, r.text
    row = r.json()["data"][0]
    assert {"id", "name", "plugin_name", "payment_type_id", "status"} <= set(row)
    # 只回字典字段：不含费率与凭证
    assert "merchant_rate" not in row and "config" not in row

    assert (await client.get("/admin-api/pay/types", headers=code_only)).status_code == 200
    assert (await client.get("/admin-api/pay/channels", headers=code_only)).status_code == 403

    channel_only = await _scoped_admin_headers(session, "chdict", ("channel.view",))
    assert (await client.get("/admin-api/pay/types", headers=channel_only)).status_code == 200
    assert (await client.get("/admin-api/pay/channel-options",
                             headers=channel_only)).status_code == 200
