"""主动查单窗口收紧测试（FREE-33）：仅扫近 N 分钟（默认 10）内创建的待支付单。

原为 24h 窗口全量入队（review P1-3：积压时 worker 空转、拖慢下单），
按运营指令改为 10 分钟窗口 + 每分钟执行。
"""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.constants.settings import SETTINGS_REGISTRY
from app.models import Order


class _Queue:
    def __init__(self):
        self.calls = []

    async def enqueue(self, name, **kwargs):
        self.calls.append((name, kwargs))


@pytest.mark.asyncio
async def test_query_window_default_is_10(client, seeded):
    assert SETTINGS_REGISTRY["query_unpaid_window"].default == 10


@pytest.mark.asyncio
async def test_scan_only_recent_pending(engine, seeded, session, monkeypatch):
    """窗口内的新单投递查单；超过 10 分钟的老待支付单不再投递。"""
    import app.worker as worker_module
    from app.worker import jobs as worker_jobs
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from sqlmodel.ext.asyncio.session import AsyncSession
    from app.core.cache import DisabledCache

    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(worker_jobs, "session_maker", maker)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_cache_ctx():
        yield DisabledCache()

    import app.core.cache as cache_module
    monkeypatch.setattr(cache_module, "get_cache_ctx", _fake_cache_ctx)

    fake_queue = _Queue()
    monkeypatch.setattr(worker_module, "queue", fake_queue)

    now = datetime.now()

    def _order(trade_no, created):
        return Order(
            trade_no=trade_no, merchant_id=seeded["merchant"].id,
            out_trade_no=f"OT{trade_no}", payment_type_id=seeded["pay_type"].id,
            payment_type_code="alipay", subject="s", money=Decimal("10.00"),
            status=0, channel_id=seeded["channel"].id,
            created_at=created, expire_at=created + timedelta(minutes=30),
        )

    session.add(_order("26100600000000000000A", now - timedelta(minutes=2)))    # 窗口内
    session.add(_order("26100600000000000000B", now - timedelta(minutes=45)))   # 超窗（待支付老单）
    await session.commit()

    await worker_jobs.scan_unpaid_orders({"job_id": "cron"})
    trades = [kw["trade_no"] for _, kw in fake_queue.calls]
    assert "26100600000000000000A" in trades
    assert "26100600000000000000B" not in trades
