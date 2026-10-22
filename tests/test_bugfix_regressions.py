"""e2e 调试发现 bug 的回归测试：

1. /agent-api/captcha 404 —— 代理登录页验证码缺失（新增 agent/common.py）
2. SAQ 定时扫描 enqueue 位置参数 TypeError —— Queue.enqueue(function, **kwargs)
3. 登录/登出/单会话撤销未传 cache → Redis 黑名单未生效（僵尸 token）
4. 首页「结算总额」误计待处理/已拒绝结算单（应仅 status=1 已打款）
5. MySQL 会话时区偏移串格式（init_command 用）
6. 路由侧 enqueue 关键字传参（回调/重发通知/补单）
7. 无回调地址/已退款单 notify_status 收口为 0（僵尸行空转）
8. 上游同步回跳跨订单重放拒绝 + 金额比对（P0）
9. 万能验证码 0000 仅 APP_ENV=local 生效（P0）
10. /storage 挂载敏感目录拒绝（P0）
"""
import hashlib
import re
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from main import app

from app.core.cache import get_cache
from app.core.security import (
    hash_password,
    is_token_revoked,
    issue_token,
    revoke_all_tokens,
    revoke_token,
)
from app.models import Order, Settlement, SystemAdmin
from tests.conftest import UPSTREAM_KEY


class FakeRedisCache:
    """语义对齐 redis.asyncio 的最小缓存（SET NX 返回值决定分布式锁成败）。"""

    def __init__(self):
        self.items = {}

    async def get(self, key: str):
        return self.items.get(key)

    async def set(self, key: str, value, nx: bool = False, ex=None, **kwargs):
        if nx and key in self.items:
            return None
        self.items[key] = value
        return True

    async def delete(self, key: str):
        self.items.pop(key, None)


class SaqLikeQueue:
    """签名对齐 SAQ Queue：enqueue(function, **kwargs)，多余位置参数即 TypeError。"""

    def __init__(self):
        self.calls = []

    async def enqueue(self, function, **kwargs):
        self.calls.append((function, kwargs))
        return "job-id"


def _use_shared_cache(cache: FakeRedisCache):
    async def _shared():
        yield cache

    app.dependency_overrides[get_cache] = _shared


def _restore_disabled_cache():
    async def _disabled():
        yield FakeRedisCache()  # 空实例：语义上等价于「恢复默认空缓存」

    app.dependency_overrides[get_cache] = _disabled


# ============================================================
# 1. 代理端验证码接口
# ============================================================

@pytest.mark.asyncio
async def test_agent_captcha_endpoint(client, seeded):
    """/agent-api/captcha 可用（回归：曾 404 导致代理登录页无验证码）。"""
    r = await client.get("/agent-api/captcha")
    assert r.status_code == 200
    body = r.json()
    assert body["code"] == 0, body
    assert body["data"]["key"]
    assert body["data"]["content"].startswith("data:image")


# ============================================================
# 2. SAQ 扫描任务以关键字参数投递
# ============================================================

@pytest.mark.asyncio
async def test_scan_jobs_enqueue_with_kwargs(engine, session, seeded, monkeypatch):
    """scan_pending_notify / scan_unpaid_orders 不再抛 enqueue TypeError。

    历史缺陷：queue.enqueue("job", trade_no) 位置传参 → SAQ TypeError，
    定时扫描每轮崩溃、通知重试与补单全部停摆。
    """
    import app.core.cache as cache_module
    import app.worker as worker_module
    from app.worker import jobs as worker_jobs
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from sqlmodel.ext.asyncio.session import AsyncSession

    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(worker_jobs, "session_maker", maker)

    cache = FakeRedisCache()

    @asynccontextmanager
    async def _fake_cache_ctx():
        yield cache

    monkeypatch.setattr(cache_module, "get_cache_ctx", _fake_cache_ctx)

    fake_queue = SaqLikeQueue()
    monkeypatch.setattr(worker_module, "queue", fake_queue)

    now = datetime.now()

    def _order(**kw):
        seq = datetime.now().strftime("%y%m%d%H%M%S%f")
        base = dict(
            trade_no=f"T{seq}",
            merchant_id=seeded["merchant"].id,
            out_trade_no=f"O{seq}",
            payment_type_code="alipay",
            subject="回归",
            money=Decimal("10.00"),
            realmoney=Decimal("10.00"),
            status=1,
            created_at=now,
            expire_at=now + timedelta(minutes=30),
            paid_at=now,
        )
        base.update(kw)
        return Order(**base)

    # a) 到期待重试通知 → notify_merchant_job
    o1 = _order(notify_status=1, next_notify_at=now - timedelta(minutes=1))
    session.add(o1)
    # b) 未到重试时间的单不投递
    session.add(_order(notify_status=1, next_notify_at=now + timedelta(minutes=5)))
    await session.commit()

    await worker_jobs.scan_pending_notify({"job_id": "cron"})
    assert fake_queue.calls == [("notify_merchant_job", {"trade_no": o1.trade_no})]

    # c) 窗口内待支付订单 → query_upstream_job
    fake_queue.calls.clear()
    o2 = _order(status=0, paid_at=None, channel_id=seeded["channel"].id)
    session.add(o2)
    await session.commit()

    await worker_jobs.scan_unpaid_orders({"job_id": "cron"})
    assert fake_queue.calls == [("query_upstream_job", {"trade_no": o2.trade_no})]


# ============================================================
# 3. 令牌撤销黑名单（需传 cache 才生效）
# ============================================================

@pytest.mark.asyncio
async def test_revoke_token_writes_blacklist(session, seeded):
    """revoke_token / revoke_all_tokens 传 cache → jti 进黑名单。"""
    cache = FakeRedisCache()
    m = seeded["merchant"]

    t1 = await issue_token(guard="merchant", user_id=m.id, session=session)
    t2 = await issue_token(guard="merchant", user_id=m.id, session=session)

    from app.core.security import decode_token
    jti1 = decode_token(t1.access_token)["jti"]

    assert await revoke_token(jti1, session, cache) is True
    assert await is_token_revoked(jti1, cache) is True
    assert await is_token_revoked(decode_token(t2.access_token)["jti"], cache) is False

    # revoke_all：批量进黑名单
    t3 = await issue_token(guard="merchant", user_id=m.id, session=session)
    jti3 = decode_token(t3.access_token)["jti"]
    n = await revoke_all_tokens("merchant", m.id, session, cache=cache)
    assert n >= 1
    assert await is_token_revoked(jti3, cache) is True


@pytest.mark.asyncio
async def test_login_single_session_kicks_old_token(client, seeded, session):
    """商户二次登录踢下线：旧 token 立即 401（回归：曾因未传 cache 依旧有效）。"""
    cache = FakeRedisCache()
    _use_shared_cache(cache)
    try:
        async def _login():
            r = await client.post("/merchant-api/auth/login", json={
                "username": "testmch", "password": "test123456", "captcha_code": "0000"})
            assert r.json()["code"] == 0, r.json()
            return r.json()["data"]["access_token"]

        old_token = await _login()
        new_token = await _login()

        r_old = await client.get("/merchant-api/auth/profile",
                                 headers={"Authorization": f"Bearer {old_token}"})
        assert r_old.status_code == 401

        r_new = await client.get("/merchant-api/auth/profile",
                                 headers={"Authorization": f"Bearer {new_token}"})
        assert r_new.status_code == 200
    finally:
        _restore_disabled_cache()


@pytest.mark.asyncio
async def test_admin_login_single_session_kicks_old_token(client, seeded, session):
    """管理员二次登录踢下线：旧 token 立即 401。"""
    admin = SystemAdmin(username="kickadmin", name="kick",
                        password=hash_password("admin123456"), is_super_admin=True)
    session.add(admin)
    await session.commit()

    cache = FakeRedisCache()
    _use_shared_cache(cache)
    try:
        async def _login():
            r = await client.post("/admin-api/auth/login", json={
                "username": "kickadmin", "password": "admin123456", "captcha_code": "0000"})
            assert r.json()["code"] == 0, r.json()
            return r.json()["data"]["access_token"]

        old_token = await _login()
        new_token = await _login()

        r_old = await client.get("/admin-api/auth/profile",
                                 headers={"Authorization": f"Bearer {old_token}"})
        assert r_old.status_code == 401

        r_new = await client.get("/admin-api/auth/profile",
                                 headers={"Authorization": f"Bearer {new_token}"})
        assert r_new.status_code == 200
    finally:
        _restore_disabled_cache()


@pytest.mark.asyncio
async def test_logout_revokes_current_token(client, seeded, session):
    """登出后当前 token 立即 401（回归：曾因未传 cache 登出形同虚设）。"""
    cache = FakeRedisCache()
    _use_shared_cache(cache)
    try:
        r = await client.post("/merchant-api/auth/login", json={
            "username": "testmch", "password": "test123456", "captcha_code": "0000"})
        token = r.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        assert (await client.get("/merchant-api/auth/profile", headers=headers)).status_code == 200
        assert (await client.post("/merchant-api/auth/logout", headers=headers)).status_code == 200
        assert (await client.get("/merchant-api/auth/profile", headers=headers)).status_code == 401
    finally:
        _restore_disabled_cache()


# ============================================================
# 4. 首页「结算总额」仅计已打款
# ============================================================

@pytest.mark.asyncio
async def test_dashboard_settle_total_paid_only(client, session, seeded):
    """settlemoney 只合计 status=1；待处理(0)/已拒绝(2)不计（回归）。"""
    admin = SystemAdmin(username="settleadmin", name="s",
                        password=hash_password("x"), is_super_admin=True)
    session.add(admin)
    m = seeded["merchant"]

    def _settle(no: str, amount: str, status: int) -> Settlement:
        return Settlement(
            settle_no=no, merchant_id=m.id, amount=Decimal(amount),
            real_amount=Decimal(amount), status=status,
        )

    session.add(_settle("SREGPAID01", "5.00", 1))    # 已打款 → 计入
    session.add(_settle("SREGPEND01", "1.00", 0))    # 待处理 → 不计
    session.add(_settle("SREGREJ01", "2.00", 2))     # 已拒绝 → 不计
    await session.commit()
    await session.refresh(admin)

    token = await issue_token(guard="admin", user_id=admin.id, session=session)
    r = await client.get("/admin-api/dashboard",
                         headers={"Authorization": f"Bearer {token.access_token}"})
    assert r.status_code == 200
    assert r.json()["data"]["settlemoney"] == 5.0


# ============================================================
# 5. /merchant 旧前缀 SPA 资源挂载
# ============================================================

@pytest.mark.asyncio
async def test_merchant_prefix_spa_entry(client):
    """/merchant/ 旧前缀可访问且返回商户中心入口 HTML（回归：曾白屏）。"""
    from pathlib import Path

    if not (Path(__file__).resolve().parent.parent / "static" / "console" / "index.html").exists():
        pytest.skip("前端未构建（static/console 缺失）")
    r = await client.get("/merchant/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_merchant_prefix_asset_mounts():
    """vite base="./" 且 SPA fallback 支持 /merchant 前缀 → 静态子目录需双挂。

    回归：曾漏挂 /merchant/assets，导致 /merchant/ 路径下入口 HTML 引用的
    相对资源全部 404、页面白屏。
    """
    from pathlib import Path
    from starlette.routing import Mount

    dist = Path(__file__).resolve().parent.parent / "static" / "console"
    if not (dist / "assets").exists():
        pytest.skip("前端未构建（static/console 缺失）")

    mounted = {r.path for r in app.routes if isinstance(r, Mount)}
    for sub in ("assets", "css", "images", "audio"):
        if (dist / sub).exists():
            assert f"/merchant/{sub}" in mounted, f"缺少 /merchant/{sub} 挂载"


# ============================================================
# 6. MySQL 会话时区偏移串
# ============================================================

def test_local_tz_offset_format():
    """偏移串 ±HH:MM 格式合法且与真实本地偏移一致（MySQL init_command 用）。"""
    from app.core.database import _local_tz_offset

    s = _local_tz_offset()
    assert re.fullmatch(r"[+-]\d{2}:\d{2}", s), s

    off = datetime.now().astimezone().utcoffset()
    total_min = int(off.total_seconds() // 60)
    sign = "+" if total_min >= 0 else "-"
    h, mm = divmod(abs(total_min), 60)
    assert s == f"{sign}{h:02d}:{mm:02d}"


# ============================================================
# 7. 路由侧 enqueue 关键字传参（回调/重发通知/补单）
# ============================================================

class SaqLikeEnqueue:
    """签名对齐 app.worker.enqueue(job_name, **kwargs)：多余位置参数即 TypeError。"""

    def __init__(self):
        self.calls = []

    async def __call__(self, job_name, **kwargs):
        self.calls.append((job_name, kwargs))
        return "job-id"


@pytest.mark.asyncio
async def test_router_enqueue_with_kwargs(client, session, seeded, monkeypatch):
    """回调/管理端重发/商户端重发均以关键字投递 notify_merchant_job。

    历史缺陷：路由侧 enqueue("notify_merchant_job", trade_no) 位置传参 →
    TypeError 被 _notify_merchant 兜底吞掉，即时通知从未入队，
    商户回调只能等 scan_pending_notify 每分钟的兜底（最长延迟 60s）。
    """
    import app.routers.admin.order as admin_order_module
    import app.routers.callbacks as callbacks_module
    import app.routers.merchant.order as merchant_order_module
    from app.core.security import issue_token

    fake = SaqLikeEnqueue()
    monkeypatch.setattr(callbacks_module, "enqueue", fake)
    monkeypatch.setattr(admin_order_module, "enqueue", fake)
    monkeypatch.setattr(merchant_order_module, "enqueue", fake)

    m = seeded["merchant"]

    # a) 支付回调路径的投递助手
    await callbacks_module._notify_merchant("T_REGRESS_1", session)
    assert fake.calls == [("notify_merchant_job", {"trade_no": "T_REGRESS_1"})]

    # b) 管理端重发通知
    now = datetime.now()
    o = Order(
        trade_no="T_REGRESS_2", merchant_id=m.id, out_trade_no="O_REGRESS_2",
        payment_type_code="alipay", subject="回归", money=Decimal("1.00"),
        realmoney=Decimal("1.00"), status=1, created_at=now,
        expire_at=now + timedelta(minutes=30), paid_at=now,
        notify_status=1, next_notify_at=now,
    )
    session.add(o)
    await session.commit()

    admin = SystemAdmin(username="enqadmin", name="e",
                        password=hash_password("x"), is_super_admin=True)
    session.add(admin)
    await session.commit()
    admin_token = await issue_token(guard="admin", user_id=admin.id, session=session)

    fake.calls.clear()
    r = await client.post("/admin-api/order/notify",
                          headers={"Authorization": f"Bearer {admin_token.access_token}"},
                          json={"trade_no": "T_REGRESS_2"})
    assert r.json()["code"] == 0, r.json()
    assert fake.calls == [("notify_merchant_job", {"trade_no": "T_REGRESS_2"})]

    # c) 商户端重发通知
    fake.calls.clear()
    m_token = await issue_token(guard="merchant", user_id=m.id, session=session)
    r = await client.post("/merchant-api/order/T_REGRESS_2/notify",
                          headers={"Authorization": f"Bearer {m_token.access_token}"})
    assert r.json()["code"] == 0, r.json()
    assert fake.calls == [("notify_merchant_job", {"trade_no": "T_REGRESS_2"})]


@pytest.mark.asyncio
async def test_notify_once_marks_done_without_url_or_unpaid(session, seeded):
    """无 notify_url / 非已支付订单：notify_merchant_once 收口 notify_status=0。

    历史缺陷：这些订单 notify_status 恒为 1，scan_pending_notify 每分钟
    重复投递空任务（僵尸行无限空转）。
    """
    from app.services.notify import notify_merchant_once

    m = seeded["merchant"]
    now = datetime.now()

    def _order(**kw):
        seq = datetime.now().strftime("%y%m%d%H%M%S%f")
        base = dict(
            trade_no=f"T{seq}", merchant_id=m.id, out_trade_no=f"O{seq}",
            payment_type_code="alipay", subject="回归", money=Decimal("1.00"),
            realmoney=Decimal("1.00"), status=1, created_at=now,
            expire_at=now + timedelta(minutes=30), paid_at=now,
            notify_status=1, next_notify_at=now,
        )
        base.update(kw)
        return Order(**base)

    # a) 已支付但无回调地址（收款页单）→ 立即置 0
    o1 = _order(notify_url=None)
    session.add(o1)
    await session.commit()
    assert await notify_merchant_once(session, trade_no=o1.trade_no) is True
    await session.refresh(o1)
    assert o1.notify_status == 0 and o1.next_notify_at is None

    # b) 已退款单（status=2）遗留 pending → 置 0
    o2 = _order(status=2, notify_url="http://127.0.0.1:1/notify")
    session.add(o2)
    await session.commit()
    assert await notify_merchant_once(session, trade_no=o2.trade_no) is True
    await session.refresh(o2)
    assert o2.notify_status == 0


# ============================================================
# 8. 上游同步回跳防跨订单重放（P0）
# ============================================================

def _sign_epay(data: dict, key: str) -> str:
    """上游 epay 协议 MD5 签名（同插件规则：排除 sign/sign_type 与空串）。"""
    items = sorted(
        (k, v) for k, v in data.items()
        if k not in ("sign", "sign_type") and v is not None and v != ""
        and not isinstance(v, (list, dict))
    )
    raw = "&".join(f"{k}={v}" for k, v in items) + key
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _epay_return_params(order, money: str, upstream_trade_no: str = "UPRET001") -> dict:
    """构造上游同步回跳的带签名参数（与真实上游 302 回跳一致）。"""
    params = {
        "pid": "2001",
        "name": "回归",
        "money": money,
        "out_trade_no": order.trade_no,
        "trade_no": upstream_trade_no,
        "type": "alipay",
        "trade_status": "TRADE_SUCCESS",
    }
    params["sign"] = _sign_epay(params, UPSTREAM_KEY)
    params["sign_type"] = "MD5"
    return params


async def _mk_pending_order(session, seeded, trade_no: str, amount: str) -> Order:
    now = datetime.now()
    o = Order(
        trade_no=trade_no, merchant_id=seeded["merchant"].id, out_trade_no=f"O{trade_no}",
        payment_type_code="alipay", subject="回归", money=Decimal(amount),
        realmoney=Decimal(amount), status=0, channel_id=seeded["channel"].id,
        notify_url="https://merchant.example/notify",
        created_at=now, expire_at=now + timedelta(minutes=30),
    )
    session.add(o)
    await session.commit()
    await session.refresh(o)
    return o


async def _fetch_order(trade_no: str):
    """从应用使用的同一测试库读取订单（绕过本测试 session 的缓存）。"""
    from app.core.database import get_db
    from app.services.order import get_order_by_trade_no
    override = app.dependency_overrides[get_db]
    async for s in override():
        return await get_order_by_trade_no(s, trade_no)
    return None


@pytest.mark.asyncio
async def test_upstream_return_replay_rejected(client, session, seeded, monkeypatch):
    """回跳防重放（P0 回归）：签名只覆盖 params、不绑定 URL 订单——

    历史缺陷：B 单支付后的签名回跳参数原样请求 A 单的 /return URL，
    验签通过且金额比对被跳过（paid_realmoney=None），A 单免金入账。
    """
    import app.routers.callbacks as callbacks_module

    fake = SaqLikeEnqueue()
    monkeypatch.setattr(callbacks_module, "enqueue", fake)

    big = await _mk_pending_order(session, seeded, "TRET_BIG", "100.00")
    tiny = await _mk_pending_order(session, seeded, "TRET_TINY", "0.01")

    # a) 跨订单重放：tiny 的签名参数打向 big 的回跳 URL → 拒绝记账
    r = await client.get(f"/callbacks/epay/{big.trade_no}/return",
                         params=_epay_return_params(tiny, "0.01"))
    assert r.status_code == 200
    big_after = await _fetch_order(big.trade_no)
    assert big_after.status == 0 and big_after.paid_at is None

    # b) 同订单改小金额（参数仍自洽签名）→ 金额比对 mismatch 拒绝
    r = await client.get(f"/callbacks/epay/{big.trade_no}/return",
                         params=_epay_return_params(big, "0.01"))
    assert r.status_code == 200
    big_after = await _fetch_order(big.trade_no)
    assert big_after.status == 0 and big_after.paid_at is None

    # c) 正常回跳（订单号 + 金额一致）→ 照常记账 + 投递通知
    r = await client.get(f"/callbacks/epay/{tiny.trade_no}/return",
                         params=_epay_return_params(tiny, "0.01"))
    assert r.status_code == 200
    tiny_after = await _fetch_order(tiny.trade_no)
    assert tiny_after.status == 1 and tiny_after.paid_at is not None
    assert ("notify_merchant_job", {"trade_no": tiny.trade_no}) in fake.calls


# ============================================================
# 9. 万能验证码 0000 仅 APP_ENV=local 生效（P0）
# ============================================================

@pytest.mark.asyncio
async def test_captcha_master_code_local_only(session, seeded, monkeypatch):
    """历史缺陷：config 默认 local，生产漏配 APP_ENV 时 0000 放行所有登录。"""
    from app.core.config import config
    from app.lib.captcha import verify_captcha_code

    cache = FakeRedisCache()
    # 测试环境（conftest 钉住 local）：0000 放行
    assert await verify_captcha_code(cache, "", "0000") is None

    monkeypatch.setattr(config, "APP_ENV", "prod")
    assert await verify_captcha_code(cache, "", "0000") == "请输入验证码"
    # 非万能码仍走正常校验路径（无缓存码 → 过期报错），不受环境切换影响
    assert await verify_captcha_code(cache, "", "1234") == "请输入验证码"


# ============================================================
# 10. /storage 挂载敏感目录拒绝（P0）
# ============================================================

def test_storage_static_mount_blocks_sensitive_dirs(tmp_path):
    """历史缺陷：/storage 无鉴权挂载整个 storage 根，app.log（含收银台令牌
    路径/jti）公网可读。上传文件（image/… 等）必须照常可访问。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from main import GuardedStaticFiles

    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "app.log").write_text("secret-log", encoding="utf-8")
    (tmp_path / "image").mkdir()
    (tmp_path / "image" / "a.txt").write_text("ok", encoding="utf-8")
    for d in ("private", "certs", "exports", "tmp"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "x.txt").write_text("secret", encoding="utf-8")

    fa = FastAPI()
    fa.mount("/storage", GuardedStaticFiles(directory=tmp_path))
    with TestClient(fa) as c:
        assert c.get("/storage/image/a.txt").status_code == 200
        for d in ("logs", "private", "certs", "exports", "tmp"):
            assert c.get(f"/storage/{d}/x.txt").status_code == 404, d
