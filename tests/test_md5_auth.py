"""开放 API MD5 认证（默认算法）测试。

- verify_api_signature：正确 / 错密钥 / 篡改金额 / 缺 sign_type / 非法 sign_type /
  小写 md5 / 商户未配密钥 / 时间戳缺失与过期 / RSA2 未开启
- 签名原文规范化：money 数字与两位小数拼串一致
- 重放一次性：Redis SET NX 语义
- E2E：MD5 下单成功并落库 order.sign_type、表单提交、缺 sign_type 拒绝
- 通知：MD5 订单用商户密钥签名，按订单算法验签
"""
import hashlib
import time
from types import SimpleNamespace

import pytest

from app.lib.sign import get_sign_content, make_md5_sign, verify_md5_sign
from app.logic.api_verify import ensure_not_replayed, verify_api_signature
from app.services.apikey import get_api_secret, set_api_secret
from app.services.crypto import encrypt_config
from tests.conftest import MERCHANT_API_SECRET, md5_params


def _merchant(secret: str | None = MERCHANT_API_SECRET, pub: str | None = None,
              rsa2_enable: bool = False):
    m = SimpleNamespace(public_key=pub, rsa2_enable=rsa2_enable, api_secret=None)
    if secret:
        m.api_secret = encrypt_config({"k": secret})
    return m


def _signed(payload: dict, secret: str = MERCHANT_API_SECRET, sign_type: str = "MD5") -> dict:
    p = dict(payload)
    p.setdefault("timestamp", str(int(time.time())))
    p["sign"] = make_md5_sign(p, secret)
    p["sign_type"] = sign_type
    return p


# ---- verify_api_signature ----

def test_valid_md5_signature_passes():
    params = _signed({"pid": "1001", "type": "alipay", "money": "10.00"})
    assert verify_api_signature(_merchant(), params) == "MD5"


def test_sign_type_lowercase_accepted():
    params = _signed({"pid": "1001"}, sign_type="md5")
    assert verify_api_signature(_merchant(), params) == "MD5"


def test_wrong_secret_rejected():
    params = _signed({"pid": "1001"}, secret="f" * 32)
    with pytest.raises(ValueError, match="MD5签名验证失败"):
        verify_api_signature(_merchant(), params)


def test_tampered_amount_rejected():
    params = _signed({"pid": "1001", "money": "10.00"})
    params["money"] = "0.01"
    with pytest.raises(ValueError, match="MD5签名验证失败"):
        verify_api_signature(_merchant(), params)


def test_missing_sign_type_rejected():
    params = _signed({"pid": "1001"})
    params.pop("sign_type")
    with pytest.raises(ValueError, match="缺少sign_type参数"):
        verify_api_signature(_merchant(), params)


def test_unknown_sign_type_rejected():
    params = _signed({"pid": "1001"})
    params["sign_type"] = "SHA256"
    with pytest.raises(ValueError, match="不支持的签名类型"):
        verify_api_signature(_merchant(), params)


def test_missing_md5_secret_rejected():
    params = _signed({"pid": "1001"})
    with pytest.raises(ValueError, match="未配置MD5密钥"):
        verify_api_signature(_merchant(secret=None), params)


def test_rsa2_disabled_rejected():
    params = _signed({"pid": "1001"}, secret=MERCHANT_API_SECRET)
    params["sign_type"] = "RSA2"  # 签名值无关，先拦开通状态
    with pytest.raises(ValueError, match="未开启RSA2签名"):
        verify_api_signature(_merchant(pub="PK"), params)


def test_missing_timestamp_rejected():
    params = {"pid": "1001", "sign_type": "MD5"}
    params["sign"] = make_md5_sign(params, MERCHANT_API_SECRET)
    with pytest.raises(ValueError, match="timestamp"):
        verify_api_signature(_merchant(), params)


def test_stale_timestamp_rejected():
    params = _signed({"pid": "1001"}, )
    params["timestamp"] = str(int(time.time()) - 1000)
    params["sign"] = make_md5_sign(params, MERCHANT_API_SECRET)
    with pytest.raises(ValueError, match="时间戳"):
        verify_api_signature(_merchant(), params)


# ---- 签名原文规范化 ----

def test_money_canonicalized():
    """数字 10 / 字符串 "10" / 10.0 与 "10.00" 拼出同一原文（方便对接）。"""
    assert get_sign_content({"money": 10}) == "money=10.00"
    assert get_sign_content({"money": "10"}) == "money=10.00"
    assert get_sign_content({"money": 10.0}) == "money=10.00"
    assert get_sign_content({"money": "10.00"}) == "money=10.00"


def test_md5_sign_roundtrip_and_case():
    data = {"pid": "1001", "money": "1.00", "type": "alipay"}
    sign = make_md5_sign(data, MERCHANT_API_SECRET)
    assert len(sign) == 32 and sign == sign.lower()
    assert verify_md5_sign(data, MERCHANT_API_SECRET, sign.upper()) is True
    assert verify_md5_sign(data, "other-secret", sign) is False
    # 与 epay 口径一致：md5(原文 + 密钥)
    raw = get_sign_content(data) + MERCHANT_API_SECRET
    assert sign == hashlib.md5(raw.encode("utf-8")).hexdigest()


def test_secret_encrypted_at_rest():
    m = SimpleNamespace(api_secret=None)
    raw = set_api_secret(m)
    assert raw and len(raw) == 32
    assert raw not in (m.api_secret or "")  # 落库为密文
    assert get_api_secret(m) == raw


# ---- 重放 ----

class _FakeRedis:
    def __init__(self):
        self.keys: dict = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.keys:
            return None
        self.keys[key] = value
        return True


@pytest.mark.asyncio
async def test_replay_rejected():
    cache = _FakeRedis()
    params = _signed({"pid": "1001"})
    await ensure_not_replayed(cache, 1, params)
    with pytest.raises(ValueError, match="请求重复提交"):
        await ensure_not_replayed(cache, 1, params)


# ---- E2E ----

@pytest.mark.asyncio
async def test_md5_order_created_and_sign_type_stored(client, seeded):
    resp = await client.post("/api/orders", json=md5_params({
        "pid": "1001", "type": "alipay", "out_trade_no": "MD5E2E1",
        "notify_url": "https://merchant.example/notify",
        "return_url": "https://merchant.example/return",
        "name": "测试商品", "money": "10",
    }))
    body = resp.json()
    assert body["code"] == 0, body
    trade_no = body["data"]["trade_no"]

    from app.services.order import get_order_by_trade_no
    from app.core.database import get_db
    from main import app as _app

    order = None
    async for s in _app.dependency_overrides[get_db]():
        order = await get_order_by_trade_no(s, trade_no)
        break
    assert order is not None and order.sign_type == "MD5"


@pytest.mark.asyncio
async def test_md5_form_encoded_request_accepted(client, seeded):
    params = md5_params({
        "pid": "1001", "type": "alipay", "out_trade_no": "MD5FORM1",
        "name": "表单商品", "money": "2.00",
    })
    resp = await client.post("/api/orders", data=params)
    body = resp.json()
    assert body["code"] == 0, body


@pytest.mark.asyncio
async def test_missing_sign_type_http_rejected(client, seeded):
    params = md5_params({
        "pid": "1001", "type": "alipay", "out_trade_no": "NOSIGNTYPE",
        "name": "测试商品", "money": "1.00",
    })
    params.pop("sign_type")
    resp = await client.post("/api/orders", json=params)
    body = resp.json()
    assert resp.status_code == 400
    assert body["code"] == -3
    assert "sign_type" in body["message"]


def test_md5_webhook_signed_with_merchant_secret():
    from decimal import Decimal

    from app.models import Merchant, Order
    from app.services.order import build_webhook_payload

    m = Merchant(pid=1001, username="u", password="x", api_secret=encrypt_config({"k": MERCHANT_API_SECRET}))
    order = Order(trade_no="T1", merchant_id=1, out_trade_no="O1", subject="商品",
                  money=Decimal("5.00"), payment_type_code="alipay", sign_type="MD5")
    payload = build_webhook_payload(order, m, platform_rsa_key="unused")
    assert payload["sign_type"] == "MD5"
    assert verify_md5_sign(payload, MERCHANT_API_SECRET, payload["sign"]) is True


def test_rsa2_webhook_still_uses_platform_key():
    from decimal import Decimal

    from app.models import Merchant, Order
    from app.lib.sign import make_rsa_sign
    from app.services.order import build_webhook_payload
    from tests.conftest import PLATFORM_PRIVATE_KEY

    m = Merchant(pid=1001, username="u", password="x")
    order = Order(trade_no="T2", merchant_id=1, out_trade_no="O2", subject="商品",
                  money=Decimal("5.00"), payment_type_code="alipay", sign_type="RSA2")
    payload = build_webhook_payload(order, m, platform_rsa_key=PLATFORM_PRIVATE_KEY)
    assert payload["sign_type"] == "RSA2"
    data = {k: v for k, v in payload.items() if k not in ("sign", "sign_type")}
    assert make_rsa_sign(data, PLATFORM_PRIVATE_KEY) == payload["sign"]


# ---- 商户面板接口 ----

async def _merchant_headers(session) -> dict:
    from sqlmodel import select

    from app.core.security import issue_token
    from app.models import Merchant

    m = (await session.exec(select(Merchant).where(Merchant.pid == 1001))).one()
    tk = await issue_token(guard="merchant", user_id=m.id, session=session)
    return {"Authorization": f"Bearer {tk.access_token}"}


@pytest.mark.asyncio
async def test_apikey_shows_secret_and_rsa2_state(client, seeded, session):
    resp = await client.get("/merchant-api/account/apikey", headers=await _merchant_headers(session))
    body = resp.json()
    assert body["code"] == 0, body
    assert body["data"]["api_secret"] == MERCHANT_API_SECRET
    assert body["data"]["rsa2_enable"] is True
    # 平台公钥可见（RSA2 通知验签用）
    assert body["data"]["platform_public_key"]


@pytest.mark.asyncio
async def test_sign_debug_returns_content_and_md5(client, seeded, session):
    resp = await client.post("/merchant-api/account/sign-debug",
                             json={"params": {"pid": "1001", "money": "1.00", "type": "alipay"}},
                             headers=await _merchant_headers(session))
    body = resp.json()
    assert body["code"] == 0, body
    data = body["data"]
    assert data["sign_content"] == "money=1.00&pid=1001&type=alipay"
    assert data["algorithms"]["MD5"]["sign"] == make_md5_sign(
        {"pid": "1001", "money": "1.00", "type": "alipay"}, MERCHANT_API_SECRET)


@pytest.mark.asyncio
async def test_rsa2_toggle_off_rejects_rsa2_request(client, seeded, session):
    headers = await _merchant_headers(session)
    resp = await client.post("/merchant-api/account/rsa2", json={"enable": False}, headers=headers)
    assert resp.json()["code"] == 0, resp.json()

    params = {
        "pid": "1001", "type": "alipay", "out_trade_no": "RSAOFF1",
        "name": "测试商品", "money": "1.00",
    }
    from tests.conftest import signed_params
    resp = await client.post("/api/orders", json=signed_params(params))
    body = resp.json()
    assert resp.status_code == 400
    assert "未开启RSA2签名" in body["message"]

    # 恢复开启，RSA2 立即恢复可用
    await client.post("/merchant-api/account/rsa2", json={"enable": True}, headers=headers)
    resp = await client.post("/api/orders", json=signed_params({**params, "out_trade_no": "RSAOFF2"}))
    assert resp.json()["code"] == 0, resp.json()
