"""pytest fixtures：sqlite 异步库 + 种子数据 + mock 上游 + ASGI 客户端。"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import httpx
import itertools
import pytest
import pytest_asyncio

_db_counter = itertools.count()

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

# 必须在导入 app 之前设置
os.environ.setdefault("APP_NAME", "pay-gateway-test")
os.environ.setdefault("APP_URL", "http://testserver")
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-0123456789abcdef")
os.environ.setdefault("STORAGE_LOCAL_SECRET", "test-storage-secret")
os.environ.setdefault("DB_SCHEME", "sqlite")
os.environ.setdefault("DB_DATABASE", str(Path(tempfile.gettempdir()) / "pay_pytest.db"))
os.environ.setdefault("REDIS_HOST", "localhost")

from sqlmodel import SQLModel  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker  # noqa: E402
from sqlmodel.ext.asyncio.session import AsyncSession  # noqa: E402

from app.core.config import config  # noqa: E402
from app.core.database import get_db  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.lib.sign import make_rsa_sign, pem_to_flat  # noqa: E402

# ------------------------------------------------------------
# 设置缓存失效包装（必须在测试模块导入前生效）：
# 生产链路 set_settings(..., cache) 末尾会 invalidate_cache；
# 测试直调 set_settings(session, values) 不带 cache —— 假缓存里的设置
# 快照会残留旧值。这里包一层：写入后失效所有已注册假缓存中的设置快照，
# 让「测试中途改设置 → 后续请求读到新值」与生产行为一致。
# ------------------------------------------------------------
import app.services.settings as _settings_mod  # noqa: E402

_test_caches: list = []
_set_settings_original = _settings_mod.set_settings


async def _set_settings_with_invalidation(session, values, cache=None):
    await _set_settings_original(session, values, cache=cache)
    key = _settings_mod._cache_key()
    for c in _test_caches:
        try:
            await c.delete(key)
        except Exception:
            pass


_settings_mod.set_settings = _set_settings_with_invalidation

from app.models import (  # noqa: E402
    Channel,
    Merchant,
    MerchantRecord,
    NotifyLog,
    Order,
    PaymentType,
    Setting,
)
from app.services.crypto import encrypt_config  # noqa: E402


def _gen_keypair_flat() -> tuple[str, str]:
    """生成 RSA-2048 测试密钥对，返回 (私钥, 公钥) 均为单行 base64。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("utf-8")
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    return pem_to_flat(priv), pem_to_flat(pub)


# 共享测试密钥对（模块级一次生成，全测试复用）
# 商户密钥对：seed 商户 public_key 用公钥，开放 API 请求用私钥签名
MERCHANT_PRIVATE_KEY, MERCHANT_PUBLIC_KEY = _gen_keypair_flat()
# 平台密钥对：seed platform_rsa_private_key 用私钥，webhook/回跳签名用
PLATFORM_PRIVATE_KEY, PLATFORM_PUBLIC_KEY = _gen_keypair_flat()

# 兼容旧导入名：商户 API 签名密钥（现为 RSA 私钥）
MERCHANT_KEY = MERCHANT_PRIVATE_KEY
# 上游通道密钥（上游协议 MD5，与商户 API 无关）
UPSTREAM_KEY = "upstreamkey456"


def signed_params(params: dict, private_key: str | None = None,
                  timestamp: int | str | None = None) -> dict:
    """开放 API 签名 helper：补 timestamp → 拼串 → RSA2 签名。"""
    p = dict(params)
    p.setdefault("timestamp", str(int(time.time()) if timestamp is None else timestamp))
    p["sign"] = make_rsa_sign(p, private_key or MERCHANT_PRIVATE_KEY)
    p["sign_type"] = "RSA2"
    return p


async def pay_code(session, pid: int = 1001, agent_id: int = 0) -> str:
    """收款页短码（/p/{code}）：按 pid/agent_id 取码，测试辅助。"""
    from sqlmodel import select

    from app.services import paytoken

    m = (await session.exec(select(Merchant).where(Merchant.pid == pid))).first()
    return await paytoken.get_or_create(session, m.id, agent_id)

# mock 上游可变状态（查单结果 / 退款结果）
upstream_state = {"query_status": 0, "refund_ok": True}


def _mock_upstream_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("skpay.php"):
        # 高盛支付（sk 协议）：POST JSON
        try:
            body = json.loads(request.content.decode("utf-8") or "{}")
        except Exception:
            body = {}
        fun = request.url.params.get("fun")
        if fun == "createOrder":
            order_no = str(body.get("orderNo", ""))
            return httpx.Response(200, json={
                "code": 200, "msg": "success",
                "data": {
                    "sys_order_id": "SK" + order_no,
                    "orderNo": order_no,
                    "payUrl": "https://upstream.example/skpay/" + order_no,
                    "amount": 10.00,
                    "sign": "mock",
                },
            })
        if fun == "queryOrder":
            return httpx.Response(200, json={
                "code": 200, "msg": "获取成功",
                "data": {
                    "sys_order_id": str(body.get("sys_order_id", "")),
                    "orderNo": "",
                    "amount": "10.00",
                    "orderState": str(upstream_state["query_status"]),
                    "sign": "mock",
                },
            })
        return httpx.Response(200, json={"code": 400, "msg": "unknown fun"})
    if path.endswith("mapi.php"):
        return httpx.Response(200, json={"code": 1, "payurl": "https://upstream.example/pay/abc123"})
    if path.endswith("api.php"):
        act = request.url.params.get("act")
        if act == "order":
            status = upstream_state["query_status"]
            return httpx.Response(200, json={
                "code": 1,
                "trade_no": "UP" + (request.url.params.get("out_trade_no") or ""),
                "out_trade_no": request.url.params.get("out_trade_no", ""),
                "status": status,
                "money": "100",
            })
        if act == "refund":
            if upstream_state["refund_ok"]:
                return httpx.Response(200, json={"code": 0, "msg": "退款成功"})
            return httpx.Response(200, json={"code": -1, "msg": "上游余额不足"})
    return httpx.Response(200, json={"code": -1, "msg": "unknown"})


@pytest_asyncio.fixture
async def engine():
    # 每个用例独立 DB 文件：Windows 下无法删除仍被占用的 sqlite 文件，
    # 复用同名文件会出现 WinError 32
    db_file = Path(tempfile.gettempdir()) / f"pay_pytest_{os.getpid()}_{next(_db_counter)}.db"
    eng = create_async_engine(
        f"sqlite+aiosqlite:///{db_file}",
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield eng
    await eng.dispose()
    try:
        if os.path.exists(db_file):
            os.remove(db_file)
    except PermissionError:
        pass


@pytest_asyncio.fixture
async def session(engine):
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s


@pytest_asyncio.fixture
async def seeded(session):
    """种子：商户（RSA 公钥已配）+ 平台私钥 + 支付方式 + epay 通道（幂等：先清理旧数据）。"""
    from sqlalchemy import delete
    await session.exec(delete(Order.__table__))
    await session.exec(delete(MerchantRecord.__table__))
    await session.exec(delete(NotifyLog.__table__))
    await session.exec(delete(Channel.__table__))
    await session.exec(delete(Merchant.__table__))
    await session.exec(delete(PaymentType.__table__))
    await session.commit()

    m = Merchant(
        pid=1001, username="testmch", password=hash_password("test123456"),
        public_key=MERCHANT_PUBLIC_KEY, status=1,
    )
    session.add(m)
    # 平台 RSA 私钥（webhook/回跳签名）
    session.add(Setting(key="platform_rsa_private_key", value=PLATFORM_PRIVATE_KEY))
    pt = PaymentType(code="alipay", name="支付宝", status=1, sort=1)
    session.add(pt)
    await session.commit()
    await session.refresh(m)
    await session.refresh(pt)
    ch = Channel(
        name="测试通道", plugin_name="epay", payment_type_id=pt.id,
        merchant_rate=2, cost_rate=1, status=1, mode=0,
        min_amount=1, max_amount=0, day_limit=0,
        config=encrypt_config({
            "appurl": "https://upstream.example/",
            "appid": "2001",
            "appkey": UPSTREAM_KEY,
            "appswitch": "1",
        }),
    )
    session.add(ch)
    await session.commit()
    await session.refresh(ch)
    return {"merchant": m, "pay_type": pt, "channel": ch}


@pytest_asyncio.fixture
async def client(engine, seeded, monkeypatch):
    """ASGI HTTP 客户端（依赖注入测试库 + mock 上游插件 HTTP）。"""
    from main import app

    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _get_db_test():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_db] = _get_db_test

    # 无操作假缓存替代真 Redis：避免测试连全局 Redis / 连接池跨事件循环复用。
    # （生产语义：Redis 不可用时 get_cache 抛 CacheUnavailableError，不降级；
    #   这里注入 no-op 假缓存仅为了在无 Redis 的测试环境跑通依赖链。）
    from app.core.cache import get_cache

    class NoopCache:
        """内存存取 + 真实计数语义的最小缓存（对齐真 Redis 常用子集）。

        set 返回真值（try_lock 视为拿到锁）；incr 为真实计数
        （扣量/限次等依赖 INCR 累计，恒 1 会让模运算永远不命中）。
        """

        def __init__(self):
            self._d = {}

        async def get(self, key):
            return self._d.get(key)

        async def set(self, key, value, **kwargs):
            self._d[key] = value
            return True

        async def delete(self, key):
            self._d.pop(key, None)
            return None

        async def incr(self, key):
            n = int(self._d.get(key, 0) or 0) + 1
            self._d[key] = n
            return n

        async def decr(self, key):
            n = int(self._d.get(key, 0) or 0) - 1
            self._d[key] = n
            return n

        async def ttl(self, key):
            return -1

        async def expire(self, key, seconds):
            return None

        async def keys(self, pattern):
            return []

    cache = NoopCache()  # 整个测试共享一个实例：INCR 计数跨请求累计（对齐真 Redis）
    _test_caches.clear()
    _test_caches.append(cache)

    async def _get_cache_test():
        yield cache

    app.dependency_overrides[get_cache] = _get_cache_test

    # mock 共享 httpx 客户端（插件用）
    from app.services import http_client as http_client_module

    def _mock_client():
        return httpx.AsyncClient(
            transport=httpx.MockTransport(_mock_upstream_handler),
            timeout=5,
        )

    monkeypatch.setattr(http_client_module, "get_http_client", _mock_client)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c

    app.dependency_overrides.clear()
