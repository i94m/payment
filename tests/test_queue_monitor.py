"""队列监控告警测试（SAQ 可观测性补强）。

覆盖：
- send_admin_alert 落后台通知中心（admin_messages）
- 通知中心 API：list / unread 过滤 / unread-count / read 单条与全部
- queue_monitor_task：首轮建基线不告警 → 耗尽新增/失败增量/僵死回收/积压告警 → 无变化不重复
- alert_queue_enable 关闭整体跳过
- POST /admin-api/queue/stats：401 / 非超管 403 / 超管 200 结构
"""
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.core.security import hash_password, issue_token
from app.models import AdminMessage, Order, Setting, SystemAdmin

from sqlmodel import select


# ---------------- fakes ----------------

class _FakeCache:
    """带 TTL 语义的内存缓存（锁/基线都依赖过期）。"""

    def __init__(self):
        self.store: dict = {}
        self.expires: dict = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and self.store.get(key) is not None and self.expires.get(key, 0) > time.time():
            return None
        self.store[key] = value
        self.expires[key] = (time.time() + ex) if ex else 0
        return True

    async def get(self, key):
        exp = self.expires.get(key, 0)
        if exp and exp < time.time():
            return None
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)
        self.expires.pop(key, None)


class _FakeSaqQueue:
    def __init__(self, workers, queued=0, active=0, scheduled=0):
        self.workers = workers
        self.queued = queued
        self.active = active
        self.scheduled = scheduled

    async def info(self):
        return {
            "name": "default",
            "workers": self.workers,
            "queued": self.queued,
            "active": self.active,
            "scheduled": self.scheduled,
        }


def _worker_stats(failed=0, aborted=0, complete=0):
    return {
        "queue_key": None,
        "stats": {"complete": complete, "failed": failed, "retried": 0,
                  "aborted": aborted, "uptime": 1000},
        "metadata": {},
    }


def _exhausted_order(seed, trade_no, paid_at=None):
    return Order(
        trade_no=trade_no, merchant_id=seed["merchant"].id,
        out_trade_no=f"OT{trade_no}", payment_type_id=seed["pay_type"].id,
        payment_type_code="alipay", subject="s", money=Decimal("10.00"),
        status=1, channel_id=seed["channel"].id,
        notify_status=-1, paid_at=paid_at or datetime.now(),
        created_at=datetime.now(), expire_at=datetime.now() + timedelta(minutes=30),
    )


# ---------------- 告警服务（通知中心落库） ----------------

@pytest.mark.asyncio
async def test_alert_writes_notification_center(session):
    from app.services.alert import send_admin_alert

    await send_admin_alert(session, "商户通知重试耗尽", "近 24 小时通知重试耗尽订单 3 笔")
    rows = (await session.exec(select(AdminMessage))).all()
    assert len(rows) == 1
    assert rows[0].title == "商户通知重试耗尽"
    assert "3 笔" in rows[0].content
    assert rows[0].biz_type == "alert"
    assert rows[0].read_at is None  # 未读，待后台通知中心展示


@pytest.mark.asyncio
async def test_admin_message_api(session, client, seeded):
    """通知中心 API：列表 / 未读过滤 / 未读数 / 单条与全部已读（全局语义）。"""
    session.add(SystemAdmin(
        name="管理员", username="admin",
        password=hash_password("admin123"), is_super_admin=True,
    ))
    session.add(AdminMessage(title="t1", content="c1", biz_type="alert"))
    session.add(AdminMessage(title="t2", content="c2", biz_type="alert"))
    session.add(AdminMessage(title="t3", content="c3", biz_type="alert", read_at=datetime.now()))
    await session.commit()

    admin = (await session.exec(
        select(SystemAdmin).where(SystemAdmin.username == "admin")
    )).first()
    headers = {"Authorization": f"Bearer {(await issue_token(guard='admin', user_id=admin.id, session=session)).access_token}"}

    # 未登录 401
    resp = await client.get("/admin-api/message/unread-count")
    assert resp.status_code == 401

    # 列表（倒序）+ 未读过滤
    resp = await client.get("/admin-api/message/list", headers=headers)
    data = resp.json()["data"]
    assert data["total"] == 3 and data["items"][0]["title"] == "t3"
    resp = await client.get("/admin-api/message/list", headers=headers, params={"unread": 1})
    assert resp.json()["data"]["total"] == 2

    # 未读数 → 单条已读 → 未读数
    resp = await client.get("/admin-api/message/unread-count", headers=headers)
    assert resp.json()["data"]["count"] == 2
    unread_id = next(m["id"] for m in data["items"] if not m["is_read"])
    resp = await client.post("/admin-api/message/read", headers=headers, json={"id": unread_id})
    assert resp.json()["code"] == 0
    resp = await client.get("/admin-api/message/unread-count", headers=headers)
    assert resp.json()["data"]["count"] == 1

    # 全部已读
    resp = await client.post("/admin-api/message/read", headers=headers, json={"id": 0})
    assert resp.json()["code"] == 0
    resp = await client.get("/admin-api/message/unread-count", headers=headers)
    assert resp.json()["data"]["count"] == 0


# ---------------- 监控 cron ----------------

@pytest.mark.asyncio
async def test_monitor_alert_flow(engine, seeded, session, monkeypatch):
    import app.core.cache as cache_module
    import app.worker as worker_module
    from app.worker import jobs as worker_jobs
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from sqlmodel.ext.asyncio.session import AsyncSession

    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(worker_jobs, "session_maker", maker)

    fake_cache = _FakeCache()

    @asynccontextmanager
    async def _fake_cache_ctx():
        yield fake_cache

    monkeypatch.setattr(cache_module, "get_cache_ctx", _fake_cache_ctx)

    fake_queue = _FakeSaqQueue(workers={"w1": _worker_stats()})
    monkeypatch.setattr(worker_module, "queue", fake_queue)

    sent: list[tuple[str, str]] = []

    from app.services import alert as alert_module

    async def _capture_alert(s, title, content):
        sent.append((title, content))

    monkeypatch.setattr(alert_module, "send_admin_alert", _capture_alert)

    session.add(_exhausted_order(seeded, "26100600000000000001A"))
    await session.commit()

    async def _run_round():
        # 生产环境锁 TTL 100s < 执行间隔 120s；测试连续执行，先模拟锁过期
        await fake_cache.delete("lock:queue_monitor")
        await worker_jobs.queue_monitor_task({"job_id": "cron"})

    # 第 1 轮：只建基线，不告警
    await _run_round()
    assert sent == []
    assert fake_cache.store.get("monitor:notify_exhausted") == 1
    assert fake_cache.store.get("monitor:saq_failed") == 0

    # 第 2 轮：耗尽新增 + 任务失败/僵死回收增量 + 积压超阈值 → 4 类告警
    session.add(_exhausted_order(seeded, "26100600000000000001B"))
    await session.commit()
    fake_queue.workers["w1"] = _worker_stats(failed=2, aborted=1)
    fake_queue.queued = 600
    fake_queue.scheduled = 10

    await _run_round()
    titles = [t for t, _ in sent]
    assert "商户通知重试耗尽" in titles
    assert "队列任务失败" in titles
    assert "僵死任务回收" in titles
    assert "队列积压" in titles

    # 第 3 轮：无变化不再重复告警
    await worker_jobs.queue_monitor_task({"job_id": "cron"})
    assert len(sent) == 4


@pytest.mark.asyncio
async def test_monitor_worker_restart_no_false_alert(engine, seeded, session, monkeypatch):
    """worker 重启计数清零（failed 下降）只下调基线，不产生误报。"""
    import app.core.cache as cache_module
    import app.worker as worker_module
    from app.worker import jobs as worker_jobs
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from sqlmodel.ext.asyncio.session import AsyncSession

    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(worker_jobs, "session_maker", maker)

    fake_cache = _FakeCache()

    @asynccontextmanager
    async def _fake_cache_ctx():
        yield fake_cache

    monkeypatch.setattr(cache_module, "get_cache_ctx", _fake_cache_ctx)

    fake_queue = _FakeSaqQueue(workers={"w1": _worker_stats(failed=3)})
    monkeypatch.setattr(worker_module, "queue", fake_queue)

    sent: list[tuple[str, str]] = []
    from app.services import alert as alert_module

    async def _capture_alert(s, title, content):
        sent.append((title, content))

    monkeypatch.setattr(alert_module, "send_admin_alert", _capture_alert)

    await worker_jobs.queue_monitor_task({"job_id": "cron"})   # 基线 failed=3
    fake_queue.workers["w1"] = _worker_stats(failed=0)          # 重启后清零
    await fake_cache.delete("lock:queue_monitor")
    await worker_jobs.queue_monitor_task({"job_id": "cron"})
    assert sent == []
    assert fake_cache.store.get("monitor:saq_failed") == 0


@pytest.mark.asyncio
async def test_monitor_disabled(engine, seeded, session, monkeypatch):
    """alert_queue_enable 关闭：不巡检不建基线不告警。"""
    import app.core.cache as cache_module
    import app.worker as worker_module
    from app.worker import jobs as worker_jobs
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from sqlmodel.ext.asyncio.session import AsyncSession

    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(worker_jobs, "session_maker", maker)

    fake_cache = _FakeCache()

    @asynccontextmanager
    async def _fake_cache_ctx():
        yield fake_cache

    monkeypatch.setattr(cache_module, "get_cache_ctx", _fake_cache_ctx)

    fake_queue = _FakeSaqQueue(workers={"w1": _worker_stats()}, queued=9999)
    monkeypatch.setattr(worker_module, "queue", fake_queue)

    sent: list[tuple[str, str]] = []
    from app.services import alert as alert_module

    async def _capture_alert(s, title, content):
        sent.append((title, content))

    monkeypatch.setattr(alert_module, "send_admin_alert", _capture_alert)

    session.add(Setting(key="alert_queue_enable", value=False))
    session.add(_exhausted_order(seeded, "26100600000000000002A"))
    await session.commit()

    await worker_jobs.queue_monitor_task({"job_id": "cron"})
    assert sent == []
    assert not any(k.startswith("monitor:") for k in fake_cache.store)


# ---------------- admin API ----------------

@pytest.mark.asyncio
async def test_queue_stats_api(engine, seeded, session, client, monkeypatch):
    session.add(SystemAdmin(
        name="管理员", username="admin",
        password=hash_password("admin123"), is_super_admin=True,
    ))
    session.add(SystemAdmin(
        name="子管", username="sub",
        password=hash_password("admin123"), is_super_admin=False,
    ))
    session.add(_exhausted_order(seeded, "26100600000000000003A"))
    await session.commit()

    admin = (await session.exec(
        select(SystemAdmin).where(SystemAdmin.username == "admin")
    )).first()
    sub = (await session.exec(
        select(SystemAdmin).where(SystemAdmin.username == "sub")
    )).first()
    admin_headers = {"Authorization": f"Bearer {(await issue_token(guard='admin', user_id=admin.id, session=session)).access_token}"}
    sub_headers = {"Authorization": f"Bearer {(await issue_token(guard='admin', user_id=sub.id, session=session)).access_token}"}

    fake_queue = _FakeSaqQueue(
        workers={"w1": _worker_stats(failed=1, complete=10)},
        queued=5, active=2, scheduled=3,
    )
    import app.worker as worker_module
    monkeypatch.setattr(worker_module, "queue", fake_queue)

    # 未登录 401
    resp = await client.post("/admin-api/queue/stats")
    assert resp.status_code == 401

    # 非超管且无 queue.view 权限 → HTTP 403 + body code 403（与其他 PermissionChecker 端点一致）
    resp = await client.post("/admin-api/queue/stats", headers=sub_headers)
    assert resp.status_code == 403
    assert resp.json()["code"] == 403

    # 超管：队列计数 + worker 统计 + 通知聚合
    resp = await client.post("/admin-api/queue/stats", headers=admin_headers)
    assert resp.status_code == 200, resp.json()
    data = resp.json()["data"]
    assert data["queue"]["queued"] == 5
    assert data["queue"]["active"] == 2
    assert data["queue"]["scheduled"] == 3
    assert data["queue"]["workers"][0]["uuid"] == "w1"
    assert data["queue"]["workers"][0]["stats"]["failed"] == 1
    assert data["notify"]["exhausted_24h"] == 1
    assert data["notify"]["pending"] == 0
