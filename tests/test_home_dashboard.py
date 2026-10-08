"""后台首页统计 + 快捷配置（金额路由/通道限速）测试。

首页统计点；路由行为与 Channel::getSubmitInfo 语义一致。
"""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Channel, Order, PaymentType, Setting, SystemAdmin
from app.services.balance import dmoney
from app.services.channel import choose_channel
from app.services.home_config import (
    get_ratelimit_map,
    set_payroute_item,
    set_ratelimit_one,
)
from tests.conftest import UPSTREAM_KEY
from app.services.crypto import encrypt_config

# 首页路由配置写入的 settings KV key（与 app/services/home_config.py 一致）
KEY_PAYROUTE = "payroute_config"
KEY_RATELIMIT = "channel_ratelimit"


@pytest_asyncio.fixture
async def two_channels(session: AsyncSession, seeded):
    """在 seeded（1 个 epay 通道）基础上再加一个通道。"""
    ch2 = Channel(
        name="备用通道", plugin_name="epay", payment_type_id=seeded["pay_type"].id,
        merchant_rate=2, cost_rate=1, status=1, mode=0,
        min_amount=1, max_amount=0, day_limit=0,
        config=encrypt_config({
            "appurl": "https://upstream.example/",
            "appid": "2002",
            "appkey": UPSTREAM_KEY,
            "appswitch": "1",
        }),
    )
    session.add(ch2)
    await session.commit()
    await session.refresh(ch2)
    seeded["channel2"] = ch2
    return seeded


def _mk_order(merchant_id: int, channel_id: int, type_id: int, *, status: int = 0,
              created_min: int = 0, paid: bool = False, realmoney: str = "10.00",
              profit: str = "1.00", deducted: bool = False) -> Order:
    now = datetime.now()
    return Order(
        trade_no=f"T{now.strftime('%y%m%d%H%M%S%f')}{merchant_id}{channel_id}{created_min}",
        merchant_id=merchant_id,
        out_trade_no=f"OUT{now.strftime('%y%m%d%H%M%S%f')}{channel_id}{created_min}",
        payment_type_id=type_id,
        payment_type_code="alipay",
        channel_id=channel_id,
        subject="测试",
        money=Decimal(realmoney),
        status=status,
        created_at=now - timedelta(minutes=created_min),
        expire_at=now + timedelta(minutes=30),
        paid_at=now if paid else None,
        realmoney=Decimal(realmoney),
        profit=Decimal(profit),
        is_deducted=deducted,
    )


# ============================================================
# 金额路由 / 通道限速 → choose_channel
# ============================================================

@pytest.mark.asyncio
async def test_payroute_fixed_channel_in_range(session, two_channels):
    """金额路由开启且金额在区间内 → 固定走指定通道。"""
    seeded = two_channels
    tid = seeded["pay_type"].id
    await set_payroute_item(session, tid, {
        "enable": 1, "min": "50", "max": "100", "channel": seeded["channel2"].id,
    })
    resolved = await choose_channel(session, None, tid, dmoney("66.00"))
    assert resolved is not None
    assert resolved.channel.id == seeded["channel2"].id


@pytest.mark.asyncio
async def test_payroute_out_of_range_falls_back(session, two_channels):
    """金额不在路由区间 → 正常选择（不限制时两个通道都可能，但绝不是强制固定通道的断言失败点）。

    这里直接断言：区间外金额仍能选出通道。
    """
    seeded = two_channels
    tid = seeded["pay_type"].id
    await set_payroute_item(session, tid, {
        "enable": 1, "min": "50", "max": "100", "channel": seeded["channel2"].id,
    })
    resolved = await choose_channel(session, None, tid, dmoney("10.00"))
    assert resolved is not None
    assert resolved.channel.id in {seeded["channel"].id, seeded["channel2"].id}


@pytest.mark.asyncio
async def test_payroute_disabled_ignored(session, two_channels):
    """金额路由关闭 → 不影响正常选择。"""
    seeded = two_channels
    tid = seeded["pay_type"].id
    await set_payroute_item(session, tid, {
        "enable": 0, "min": "50", "max": "100", "channel": seeded["channel2"].id,
    })
    resolved = await choose_channel(session, None, tid, dmoney("66.00"))
    assert resolved is not None


@pytest.mark.asyncio
async def test_ratelimited_channel_excluded(session, two_channels):
    """限速通道（近 60 秒订单数达上限）被剔除 → 选另一个通道；全部限速 → None。"""
    seeded = two_channels
    tid = seeded["pay_type"].id
    ch1, ch2 = seeded["channel"], seeded["channel2"]

    session.add(_mk_order(seeded["merchant"].id, ch1.id, tid, created_min=0))
    await session.commit()

    await set_ratelimit_one(session, ch1.id, 1)  # ch1 每分钟 1 单，已用完
    resolved = await choose_channel(session, None, tid, dmoney("10.00"))
    assert resolved is not None
    assert resolved.channel.id == ch2.id

    # ch2 也限速（同样需要窗口内已有订单）→ 全部被剔除，无可用通道
    session.add(_mk_order(seeded["merchant"].id, ch2.id, tid, created_min=0))
    await session.commit()
    await set_ratelimit_one(session, ch2.id, 1)
    resolved = await choose_channel(session, None, tid, dmoney("10.00"))
    assert resolved is None

    # 超出 60 秒窗口的订单不计入
    await session.exec(delete(Order.__table__))
    session.add(_mk_order(seeded["merchant"].id, ch1.id, tid, created_min=5))
    await session.commit()
    resolved = await choose_channel(session, None, tid, dmoney("10.00"))
    assert resolved is not None


@pytest.mark.asyncio
async def test_ratelimit_config_roundtrip(session, two_channels):
    """限速配置写入/清除。"""
    seeded = two_channels
    await set_ratelimit_one(session, seeded["channel"].id, 30)
    await set_ratelimit_one(session, seeded["channel2"].id, 0)  # 0 视为移除
    mapping = await get_ratelimit_map(session)
    assert mapping == {seeded["channel"].id: 30}

    await set_ratelimit_one(session, seeded["channel"].id, 0)
    assert await get_ratelimit_map(session) == {}


# ============================================================
# 首页统计 API
# ============================================================

async def _admin_headers(session) -> dict:
    admin = SystemAdmin(username="dashadmin", name="dash", password="x", is_super_admin=True)
    session.add(admin)
    await session.commit()
    await session.refresh(admin)
    from app.core.security import issue_token
    token = await issue_token(guard="admin", user_id=admin.id, session=session)
    return {"Authorization": f"Bearer {token.access_token}"}


@pytest.mark.asyncio
async def test_dashboard_stats_fields(client, session, two_channels):
    """统计点齐全：总数/余额/结算/预计收入/成功率/每日收入表。"""
    seeded = two_channels
    tid = seeded["pay_type"].id
    # 今日：3 单，2 成功（其中 1 单扣量）、1 待支付 → 成功率 66.67%
    session.add(_mk_order(seeded["merchant"].id, seeded["channel"].id, tid,
                          status=1, paid=True, created_min=0, realmoney="100.00", profit="10.00"))
    session.add(_mk_order(seeded["merchant"].id, seeded["channel"].id, tid,
                          status=1, paid=True, created_min=1, realmoney="50.00", profit="5.00",
                          deducted=True))
    session.add(_mk_order(seeded["merchant"].id, seeded["channel2"].id, tid,
                          status=0, created_min=2))
    await session.commit()

    headers = await _admin_headers(session)
    r = await client.get("/admin-api/dashboard", headers=headers)
    assert r.status_code == 200
    data = r.json()["data"]

    assert data["count1"] == 3          # 订单总数（含待支付）
    assert data["count2"] == 1          # 商户数量
    assert data["today_deduct_count"] == 1
    assert data["today_deduct_money"] == 50.0   # 预计收入 = 扣量订单金额
    assert data["success_rate"] == 66.67

    assert str(tid) in data["paytype"]  # 支付方式/通道名称映射
    assert str(seeded["channel"].id) in data["channel"]

    # 今日收入表：实收合计 150，利润合计 15
    assert data["order_today"]["all"] == 150.0
    assert data["order_today"]["profit_all"] == 15.0
    assert data["order_today"]["paytype"][str(tid)] == 150.0
    assert data["order_today"]["profit_paytype"][str(tid)] == 15.0
    # 今日金额路由的快捷配置可见
    assert "paytype" in data and "channel" in data
    assert len(data["trend"]) == 7
    assert "server_time" in data


@pytest.mark.asyncio
async def test_dashboard_payroute_and_ratelimit_api(client, session, two_channels):
    """快捷配置读写接口：金额路由保存/回显、限速保存/统计/一键关闭。"""
    seeded = two_channels
    tid = seeded["pay_type"].id
    headers = await _admin_headers(session)

    # 保存金额路由
    r = await client.post("/admin-api/dashboard/payroute", json={
        "type_id": tid, "enable": True, "min": "10", "max": "500", "channel": seeded["channel2"].id,
    }, headers=headers)
    assert r.status_code == 200

    row = (await session.exec(select(Setting).where(Setting.key == KEY_PAYROUTE))).first()
    assert row is not None and str(tid) in row.value
    assert row.value[str(tid)]["channel"] == seeded["channel2"].id
    assert row.value[str(tid)]["enable"] == 1

    # config 回显
    r = await client.get("/admin-api/dashboard/config", headers=headers)
    cfg = r.json()["data"]
    assert any(t["id"] == tid for t in cfg["pay_types"])
    assert cfg["payroute"][str(tid)]["channel"] == seeded["channel2"].id
    assert cfg["channels"]

    # 保存限速 + 统计接口
    ch1_id = seeded["channel"].id  # rollback 会过期 ORM 实例，提前取值
    r = await client.post("/admin-api/dashboard/ratelimit", json={
        "channel_id": ch1_id, "value": 5,
    }, headers=headers)
    assert r.status_code == 200
    rl = (await session.exec(select(Setting).where(Setting.key == KEY_RATELIMIT))).first()
    assert rl.value[str(ch1_id)] == 5

    r = await client.get(f"/admin-api/dashboard/ratelimit-stats?cid={seeded['channel'].id}", headers=headers)
    stats = r.json()["data"]
    assert stats["show"] == 1
    assert stats["limit"] == 5
    assert "allnum" in stats and "rate" in stats

    # 一键关闭
    r = await client.post("/admin-api/dashboard/ratelimit", json={"channel_id": 0}, headers=headers)
    assert r.status_code == 200
    await session.rollback()  # 结束本会话隐式事务，读取客户端会话写入的最新值
    assert await get_ratelimit_map(session) == {}

    # 未配置限速的通道 → show=0
    r = await client.get(f"/admin-api/dashboard/ratelimit-stats?cid={ch1_id}", headers=headers)
    assert r.json()["data"]["show"] == 0
