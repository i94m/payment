"""开放 API RSA2 认证测试：

- verify_api_signature 单元：签名通过 / 错 key / 缺 timestamp / 超 300s / body 篡改 / 未配置公钥
- derive_public_key：由私钥派生公钥（单行 base64 / PEM 双格式）
- 启动自检：平台私钥为空时自动生成、已有不覆盖
- 商户公钥：保存 / 清空（传空即清除）
- E2E：未配置公钥拒 + 响应为纯 {code,message,data} 包络（无响应签名）
"""
import base64
import time
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa as crsa
from sqlmodel import select

from app.lib.sign import (
    derive_public_key,
    make_rsa_sign,
    pem_to_flat,
    verify_rsa_sign,
)
from app.logic.api_verify import verify_api_signature
from app.models import Setting
from tests.conftest import MERCHANT_PRIVATE_KEY, MERCHANT_PUBLIC_KEY, signed_params


def _gen_keypair() -> tuple[str, str]:
    """返回 (私钥单行base64, 公钥单行base64)，与平台存储格式一致。"""
    key = crsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_b64 = base64.b64encode(key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )).decode()
    pub_flat = pem_to_flat(key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode())
    return priv_b64, pub_flat


# ---- verify_api_signature（RSA2 单算法） ----

def _signed(pub_params: dict, priv: str) -> dict:
    p = dict(pub_params)
    p.setdefault("timestamp", str(int(time.time())))
    p["sign"] = make_rsa_sign(p, priv)
    p["sign_type"] = "RSA2"
    return p


def test_valid_signature_passes():
    priv, pub = _gen_keypair()
    params = _signed({"pid": "1001", "type": "alipay"}, priv)
    verify_api_signature(pub, params)  # 不抛即通过


def test_wrong_key_rejected():
    priv, _ = _gen_keypair()
    _, other_pub = _gen_keypair()
    params = _signed({"pid": "1001", "type": "alipay"}, priv)
    with pytest.raises(ValueError, match="签名验证失败"):
        verify_api_signature(other_pub, params)


def test_missing_timestamp_rejected():
    priv, pub = _gen_keypair()
    params = {"pid": "1001", "sign_type": "RSA2"}
    params["sign"] = make_rsa_sign(params, priv)
    with pytest.raises(ValueError, match="timestamp"):
        verify_api_signature(pub, params)


def test_stale_timestamp_rejected():
    priv, pub = _gen_keypair()
    params = _signed({"pid": "1001"}, priv)
    params["timestamp"] = str(int(time.time()) - 1000)
    # 原签名对原 timestamp 有效，改时间戳后验签失败（篡改同源）
    with pytest.raises(ValueError):
        verify_api_signature(pub, params)


def test_stale_timestamp_with_valid_sign_rejected():
    """签名合法但时间戳过期 → 拒（防重放窗口）。"""
    priv, pub = _gen_keypair()
    params = _signed({"pid": "1001"}, priv)
    params["timestamp"] = str(int(time.time()) - 1000)
    params["sign"] = make_rsa_sign(
        {k: v for k, v in params.items() if k not in ("sign", "sign_type")}, priv)
    params["sign_type"] = "RSA2"
    with pytest.raises(ValueError, match="时间戳"):
        verify_api_signature(pub, params)


def test_tampered_body_rejected():
    priv, pub = _gen_keypair()
    params = _signed({"pid": "1001", "money": "10.00"}, priv)
    params["money"] = "0.01"  # 篡改已签名字段
    with pytest.raises(ValueError):
        verify_api_signature(pub, params)


def test_missing_public_key_rejected():
    priv, _ = _gen_keypair()
    params = _signed({"pid": "1001"}, priv)
    with pytest.raises(ValueError, match="未配置RSA公钥"):
        verify_api_signature(None, params)


def test_missing_sign_rejected():
    _, pub = _gen_keypair()
    with pytest.raises(ValueError, match="签名不能为空"):
        verify_api_signature(pub, {"pid": "1001", "timestamp": str(int(time.time()))})


# ---- derive_public_key ----

def test_derive_public_key_roundtrip():
    priv, pub = _gen_keypair()
    assert derive_public_key(priv) == pub
    # PEM 输入同样兼容
    pem = f"-----BEGIN PRIVATE KEY-----\n" + "\n".join(
        priv[i:i + 64] for i in range(0, len(priv), 64)
    ) + "\n-----END PRIVATE KEY-----"
    assert derive_public_key(pem) == pub
    assert derive_public_key("") == ""
    with pytest.raises(ValueError):
        derive_public_key("not-a-key!!")


# ---- 启动自检：平台私钥为空时自动生成 ----

@pytest.mark.asyncio
async def test_ensure_platform_rsa_key_generates_when_empty(session):
    from app.services.settings import ensure_platform_rsa_key

    assert (await session.exec(select(Setting))).all() == []  # 初始无配置
    assert await ensure_platform_rsa_key(session) is True

    row = (await session.exec(
        select(Setting).where(Setting.key == "platform_rsa_private_key"))).first()
    assert row is not None and row.value
    # 生成即可用：派生公钥 + 签名验签往返
    priv = str(row.value)
    pub = derive_public_key(priv)
    data = {"pid": "1001", "timestamp": str(int(time.time()))}
    assert verify_rsa_sign(data, pub, make_rsa_sign(data, priv))


@pytest.mark.asyncio
async def test_ensure_platform_rsa_key_keeps_existing(session):
    from app.services.settings import ensure_platform_rsa_key

    priv, _ = _gen_keypair()
    session.add(Setting(key="platform_rsa_private_key", value=priv))
    await session.commit()

    assert await ensure_platform_rsa_key(session) is False
    row = (await session.exec(
        select(Setting).where(Setting.key == "platform_rsa_private_key"))).first()
    assert str(row.value) == priv  # 不覆盖


# ---- 商户端公钥管理 ----

@pytest.mark.asyncio
async def test_merchant_publickey_set_and_clear(session, seeded):
    from app.routers.merchant.account import PublicKeyRequest, set_publickey

    m = seeded["merchant"]
    auth = SimpleNamespace(user=m)

    await set_publickey(PublicKeyRequest(public_key="MCH-PUB-1"), auth, session)
    assert m.public_key == "MCH-PUB-1"

    # 清空公钥
    await set_publickey(PublicKeyRequest(public_key="  "), auth, session)
    assert m.public_key is None


@pytest.mark.asyncio
async def test_rsa_generate_saves_public_key(client, session):
    """面板一键生成密钥对：公钥自动落库，私钥仅返回一次。"""
    from sqlmodel import select as _sel

    from app.models import Merchant

    headers_tokens = []
    m = (await session.exec(_sel(Merchant).where(Merchant.pid == 1001))).one()
    from app.core.security import issue_token
    tk = await issue_token(guard="merchant", user_id=m.id, session=session)
    headers_tokens.append({"Authorization": f"Bearer {tk.access_token}"})

    m.public_key = None  # 先清空
    session.add(m)
    await session.commit()

    resp = await client.post("/merchant-api/account/rsa/generate", headers=headers_tokens[0])
    body = resp.json()
    assert body["code"] == 0, body
    assert body["data"]["public_key"] and body["data"]["private_key"]

    session.expire_all()
    m2 = (await session.exec(_sel(Merchant).where(Merchant.pid == 1001))).one()
    assert m2.public_key == body["data"]["public_key"]
    # 生成的密钥对可用：私钥签名 → 公钥验签
    data = {"pid": "1001", "timestamp": str(int(time.time()))}
    assert verify_rsa_sign(data, m2.public_key, make_rsa_sign(data, body["data"]["private_key"]))


# ---- E2E：响应包络与未配置公钥 ----

@pytest.mark.asyncio
async def test_orders_response_plain_envelope(client, seeded):
    """响应为纯 {code,message,data} 包络：无 sign/sign_type/timestamp 响应签名。"""
    resp = await client.post("/orders", json=signed_params({
        "pid": "1001", "type": "alipay", "out_trade_no": "ENVE1",
        "notify_url": "https://merchant.example/notify",
        "return_url": "https://merchant.example/return",
        "name": "测试商品", "money": "10.00",
    }))
    body = resp.json()
    assert body["code"] == 0, body
    assert set(body.keys()) == {"code", "message", "data"}


@pytest.mark.asyncio
async def test_unconfigured_public_key_rejected_with_hint(client, seeded, session):
    from sqlmodel import select as _sel

    from app.models import Merchant

    m = (await session.exec(_sel(Merchant).where(Merchant.pid == 1001))).one()
    m.public_key = None
    session.add(m)
    await session.commit()

    resp = await client.post("/orders", json=signed_params({
        "pid": "1001", "type": "alipay", "out_trade_no": "NOPUB1",
        "notify_url": "https://merchant.example/notify",
        "return_url": "", "name": "测试商品", "money": "10.00",
    }))
    body = resp.json()
    assert resp.status_code == 400
    assert body["code"] != 0
    assert "RSA公钥" in body["message"]  # 明确引导面板生成


# ---- 平台公钥下发（商户验签 webhook 用） ----

@pytest.mark.asyncio
async def test_apikey_returns_platform_public_key(client, seeded, session):
    from sqlmodel import select as _sel

    from app.models import Merchant
    from tests.conftest import PLATFORM_PUBLIC_KEY

    m = (await session.exec(_sel(Merchant).where(Merchant.pid == 1001))).one()
    from app.core.security import issue_token
    tk = await issue_token(guard="merchant", user_id=m.id, session=session)

    resp = await client.get("/merchant-api/account/apikey",
                            headers={"Authorization": f"Bearer {tk.access_token}"})
    body = resp.json()
    assert body["code"] == 0, body
    assert body["data"]["pid"] == 1001
    assert body["data"]["public_key"] == MERCHANT_PUBLIC_KEY
    assert body["data"]["platform_public_key"] == PLATFORM_PUBLIC_KEY
