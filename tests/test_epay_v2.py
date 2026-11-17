"""易支付V2（epayv2）插件：RSA2 签名/验签、下单跳转、回调分支回归。"""
import time
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.lib.sign import derive_public_key, make_rsa_sign, normalize_key, pem_to_flat, verify_rsa_sign
from app.services.plugin import PluginRegistry
from app.services.plugin_interfaces import PaymentContext
from plugins.epayv2.plugin import EpayV2Payment


def _keypair() -> tuple[str, str]:
    """生成一对 RSA 密钥，返回 (单行 base64 私钥, 单行 base64 公钥)。"""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = pem_to_flat(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode())
    pub = pem_to_flat(key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode())
    return priv, pub


def _ctx() -> PaymentContext:
    return PaymentContext(
        order={
            "trade_no": "T202611170001",
            "subject": "测试商品",
            "realmoney": "9.90",
            "payment_type_code": "alipay",
        },
        notify_url="https://m.example/callbacks/epayv2/T202611170001",
        return_url="https://m.example/callbacks/epayv2/T202611170001/return",
    )


def _notify_params(platform_priv: str, **over) -> dict:
    params = {
        "pid": "1001",
        "trade_no": "2026111722001",
        "out_trade_no": "T202611170001",
        "type": "alipay",
        "name": "测试商品",
        "money": "9.90",
        "trade_status": "TRADE_SUCCESS",
        "buyer": "buyer@example.com",
        "timestamp": str(int(time.time())),
        "sign_type": "RSA",
    }
    params.update(over)
    params.pop("sign", None)
    params["sign"] = make_rsa_sign(params, platform_priv)
    return params


def test_normalize_key_accepts_multiline_textarea_paste():
    """后台 textarea 粘贴：多行 PEM / CRLF / 带空白的单行 base64 都能签名验签。"""
    priv, pub = _keypair()
    multi_pem = "-----BEGIN PRIVATE KEY-----\r\n" + "\n".join(
        priv[i:i + 64] for i in range(0, len(priv), 64)
    ) + "\r\n-----END PRIVATE KEY-----\r\n\r\n"
    assert normalize_key(multi_pem).startswith("-----BEGIN PRIVATE KEY-----\n")
    assert "\r" not in normalize_key(multi_pem)

    data = {"pid": "1001", "money": "9.90"}
    sign = make_rsa_sign(data, multi_pem)          # 多行 PEM 私钥签名
    assert verify_rsa_sign(data, " " + pub + "\n", sign)  # 单行公钥带空白验签

    spaced = "\n".join(pub[i:i + 64] for i in range(0, len(pub), 64))
    assert normalize_key(spaced) == pub            # 无头尾的换行 base64 压成单行


def test_meta_name_matches_dir():
    PluginRegistry.reload()
    meta = PluginRegistry.plugin_meta("epayv2")
    assert meta is not None
    assert meta.name == "epayv2"
    assert meta.showname == "易支付V2"
    assert meta.version == "1.0.0"
    names = [i["name"] for i in meta.inputs]
    assert names == ["appurl", "appid", "appkey", "appsecret"]
    # 公钥/私钥需要整段粘贴 → textarea；其余仍为单行 input
    kinds = {i["name"]: i["type"] for i in meta.inputs}
    assert kinds["appkey"] == "textarea"
    assert kinds["appsecret"] == "textarea"
    # 凭证字段必须带可直接照抄的 placeholder
    for item in meta.inputs:
        if item["type"] in ("input", "textarea"):
            assert item.get("placeholder")


# ============================================================
# 回调：验签 + 状态 + 金额
# ============================================================

async def test_notify_success():
    platform_priv, platform_pub = _keypair()
    plugin = EpayV2Payment({"appid": "1001", "appkey": platform_pub, "appsecret": ""})
    verdict = await plugin.notify_handler(_notify_params(platform_priv))
    assert verdict.ok is True
    assert verdict.ack == "success"
    assert verdict.money == Decimal("9.90")
    assert verdict.api_trade_no == "2026111722001"
    assert verdict.buyer_id == "buyer@example.com"


async def test_notify_rejects_tampered_money():
    platform_priv, platform_pub = _keypair()
    plugin = EpayV2Payment({"appid": "1001", "appkey": platform_pub, "appsecret": ""})
    params = _notify_params(platform_priv)
    params["money"] = "0.01"
    verdict = await plugin.notify_handler(params)
    assert verdict.ok is False
    assert verdict.ack == "fail"


async def test_notify_rejects_missing_sign_and_bad_key():
    _, platform_pub = _keypair()
    plugin = EpayV2Payment({"appid": "1001", "appkey": platform_pub, "appsecret": ""})
    params = _notify_params(_keypair()[0])
    params.pop("sign")
    assert (await plugin.notify_handler(params)).ack == "fail"

    # 平台公钥未配置：一律拒绝（不能放行）
    plugin_no_key = EpayV2Payment({"appid": "1001", "appkey": "", "appsecret": ""})
    verdict = await plugin_no_key.notify_handler(_notify_params(_keypair()[0]))
    assert verdict.ok is False and verdict.ack == "fail"


async def test_notify_rejects_expired_timestamp():
    platform_priv, platform_pub = _keypair()
    plugin = EpayV2Payment({"appid": "1001", "appkey": platform_pub, "appsecret": ""})
    old_ts = str(int(time.time()) - 301)
    verdict = await plugin.notify_handler(_notify_params(platform_priv, timestamp=old_ts))
    assert verdict.ok is False
    assert verdict.ack == "fail"


async def test_notify_non_success_status_acks_success():
    """验签通过但非成功状态 → 应答 success 让上游停止重发。"""
    platform_priv, platform_pub = _keypair()
    plugin = EpayV2Payment({"appid": "1001", "appkey": platform_pub, "appsecret": ""})
    verdict = await plugin.notify_handler(
        _notify_params(platform_priv, trade_status="WAIT_BUYER_PAY")
    )
    assert verdict.ok is False
    assert verdict.ack == "success"


async def test_notify_bad_money_fails():
    platform_priv, platform_pub = _keypair()
    plugin = EpayV2Payment({"appid": "1001", "appkey": platform_pub, "appsecret": ""})
    verdict = await plugin.notify_handler(_notify_params(platform_priv, money="abc"))
    assert verdict.ok is False
    assert verdict.ack == "fail"


async def test_return_handler_verifies():
    platform_priv, platform_pub = _keypair()
    plugin = EpayV2Payment({"appid": "1001", "appkey": platform_pub, "appsecret": ""})
    ok = await plugin.return_handler(_notify_params(platform_priv))
    assert ok == {"status": "paid", "api_trade_no": "2026111722001"}
    assert await plugin.return_handler({"foo": "bar"}) is None


# ============================================================
# 下单
# ============================================================

async def test_submit_jump_signs_with_merchant_key():
    merchant_priv, merchant_pub = _keypair()
    plugin = EpayV2Payment({
        "appurl": "https://pay.example.com/", "appid": "1001",
        "appkey": "unused-in-jump", "appsecret": merchant_priv, "appswitch": "0",
    })
    res = await plugin.submit(_ctx())
    assert res.kind == "jump"

    parsed = urlparse(res.url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == "https://pay.example.com/api/pay/submit"
    query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert query["pid"] == "1001"
    assert query["sign_type"] == "RSA"
    assert query["out_trade_no"] == "T202611170001"
    assert query["money"] == "9.90"
    assert query["notify_url"] == "https://m.example/callbacks/epayv2/T202611170001"
    # 商户私钥签名 → 派生公钥可验
    assert verify_rsa_sign(query, derive_public_key(merchant_priv), query["sign"]) is True
    assert merchant_pub  # 供静态检查保留


async def test_submit_api_uses_pay_type_pay_info(monkeypatch):
    """appswitch=1：POST api/pay/create，按 pay_type 映射 PaymentResult.kind。"""
    merchant_priv, merchant_pub = _keypair()
    platform_priv, platform_pub = _keypair()
    plugin = EpayV2Payment({
        "appurl": "https://pay.example.com/", "appid": "1001",
        "appkey": platform_pub, "appsecret": merchant_priv, "appswitch": "1",
    })
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        body = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        seen["body"] = body
        assert body["pid"] == "1001"
        assert body["type"] == "alipay"
        assert body["method"] == "web"
        assert verify_rsa_sign(body, merchant_pub, body["sign"]) is True
        payload = {
            "code": 0,
            "trade_no": "2026111722001",
            "pay_type": "qrcode",
            "pay_info": "https://pay.example.com/qr/abc",
            "timestamp": str(int(time.time())),
            "sign_type": "RSA",
        }
        payload["sign"] = make_rsa_sign(payload, platform_priv)
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(
        "plugins.epayv2.plugin.get_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5),
    )

    res = await plugin.submit(_ctx())
    assert seen["url"] == "https://pay.example.com/api/pay/create"
    assert res.kind == "qrcode"
    assert res.url == "https://pay.example.com/qr/abc"


async def test_submit_api_rejects_bad_response_sign(monkeypatch):
    merchant_priv, _ = _keypair()
    other_priv, _ = _keypair()
    _, platform_pub = _keypair()
    plugin = EpayV2Payment({
        "appurl": "https://pay.example.com/", "appid": "1001",
        "appkey": platform_pub, "appsecret": merchant_priv, "appswitch": "1",
    })

    def handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "code": 0,
            "pay_type": "jump",
            "pay_info": "https://evil.example.com/",
            "timestamp": str(int(time.time())),
        }
        payload["sign"] = make_rsa_sign(payload, other_priv)  # 非平台私钥签名
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(
        "plugins.epayv2.plugin.get_http_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5),
    )
    res = await plugin.submit(_ctx())
    assert res.kind == "error"
    assert "验签失败" in res.msg
