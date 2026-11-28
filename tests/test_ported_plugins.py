"""移植自老 epay 的三个内置插件：金科（jinke）/ RS（rspay）/ 立鼎（liding）。

覆盖：meta 表单、签名向量、下单请求、回调验签分支、同步回跳。
"""
from decimal import Decimal
from urllib.parse import parse_qs

import httpx

from app.services.plugin import PluginRegistry
from app.services.plugin_interfaces import PaymentContext
from plugins.jinke.plugin import JinkePayment
from plugins.jinke.plugin import _make_sign as jinke_sign
from plugins.liding.plugin import LidingPayment
from plugins.liding.plugin import _make_sign as liding_sign
from plugins.rspay.plugin import RsPayPayment
from plugins.rspay.plugin import _make_sign as rspay_sign

KEY = "testkey"


def _ctx() -> PaymentContext:
    return PaymentContext(
        order={
            "trade_no": "T20261128001",
            "subject": "测试商品",
            "realmoney": "9.90",
            "payment_type_code": "alipay",
        },
        notify_url="https://m.example/cb",
        return_url="https://m.example/ret",
        client_ip="1.2.3.4",
    )


def _patch_client(monkeypatch, module: str, handler):
    monkeypatch.setattr(
        module,
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5),
    )


# ============================================================
# meta / 注册
# ============================================================

def test_ok_ack_follows_plugin_literal():
    """记账成功应答：金科/RS 上游要 "OK"，立鼎沿用默认 success。"""
    from app.routers.callbacks import _ok_ack

    assert _ok_ack(JinkePayment({})) == "OK"
    assert _ok_ack(RsPayPayment({})) == "OK"
    assert _ok_ack(LidingPayment({})) == "success"


def test_meta_registered_and_form_complete():
    PluginRegistry.reload()
    expect = {
        "jinke": ("金科支付", JinkePayment),
        "rspay": ("RS支付", RsPayPayment),
        "liding": ("立鼎支付", LidingPayment),
    }
    for name, (showname, cls) in expect.items():
        meta = PluginRegistry.plugin_meta(name)
        assert meta is not None
        assert meta.name == name
        assert meta.showname == showname
        assert meta.version == "1.0.0"
        assert meta.types == ["alipay", "wxpay"]
        # inputs 与插件实际读取的 channel 键一一对应，且单行字段都带提示词
        assert [i["name"] for i in meta.inputs] == ["appurl", "appid", "appkey", "productid"]
        for item in meta.inputs:
            assert item["type"] == "input"
            assert item.get("placeholder")
        assert PluginRegistry.get_payment_class(name) is cls


# ============================================================
# 签名向量（离线比对）
# ============================================================

def test_jinke_sign_vector():
    params = {
        "mchId": "10001", "productId": "9001", "outTradeNo": "T20261128001",
        "amount": 100, "reqTime": 1700000000000,
        "notifyUrl": "https://m.example/cb", "returnUrl": "https://m.example/ret",
    }
    assert jinke_sign(params, KEY) == "5d867d7eb03207aeb4c1b284fe480882"


def test_rspay_sign_vector_is_uppercase():
    params = {
        "memberid": "10001", "orderid": "T20261128001", "amount": "9.90",
        "datetime": "2026-11-28 12:00:00", "transaction_id": "TX001", "returncode": "00",
    }
    assert rspay_sign(params, KEY) == "7C19EB97C48BBFFE459F894B1721BB85"


def test_liding_sign_vector_is_uppercase():
    params = {
        "mchId": "10001", "mchOrderNo": "T20261128001", "amount": 990,
        "payOrderId": "PO001", "channelOrderNo": "CH001", "status": "2",
    }
    assert liding_sign(params, KEY) == "4FF1E9EFFF757BA13FC1525BE12E39F2"


# ============================================================
# 金科支付
# ============================================================

def _jinke_params(**over) -> dict:
    params = {
        "mchId": "10001", "productId": "9001", "outTradeNo": "T20261128001",
        "amount": 990, "state": "1", "tradeNo": "JK001",
    }
    params.update(over)
    params["sign"] = jinke_sign(params, KEY)
    return params


async def test_jinke_notify_success():
    plugin = JinkePayment({"appid": "10001", "appkey": KEY, "productid": "9001"})
    verdict = await plugin.notify_handler(_jinke_params())
    assert verdict.ok is True
    assert verdict.ack == "OK"
    assert verdict.money == Decimal("9.90")
    assert verdict.api_trade_no == "JK001"


async def test_jinke_notify_rejects_tampered_amount():
    plugin = JinkePayment({"appkey": KEY})
    params = _jinke_params()
    params["amount"] = 1
    verdict = await plugin.notify_handler(params)
    assert verdict.ok is False
    assert verdict.ack == "FAIL"


async def test_jinke_notify_non_success_acks_ok():
    plugin = JinkePayment({"appkey": KEY})
    verdict = await plugin.notify_handler(_jinke_params(state="0"))
    assert verdict.ok is False
    assert verdict.ack == "OK"


async def test_jinke_notify_bad_money_fails():
    plugin = JinkePayment({"appkey": KEY})
    verdict = await plugin.notify_handler(_jinke_params(amount="abc"))
    assert verdict.ok is False and verdict.ack == "FAIL"


async def test_jinke_return_handler():
    plugin = JinkePayment({"appkey": KEY})
    ok = await plugin.return_handler(_jinke_params())
    assert ok["status"] == "paid"
    assert ok["out_trade_no"] == "T20261128001"
    assert ok["money"] == "9.90"
    assert ok["api_trade_no"] == "JK001"
    assert await plugin.return_handler(_jinke_params(state="0")) is None
    assert await plugin.return_handler({"foo": "bar"}) is None


async def test_jinke_submit_json_and_jump(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["content_type"] = request.headers.get("content-type", "")
        body = __import__("json").loads(request.content.decode())
        seen["body"] = body
        assert body["mchId"] == "10001"
        assert body["productId"] == "9001"
        assert body["amount"] == 990
        assert body["outTradeNo"] == "T20261128001"
        assert body["notifyUrl"] == "https://m.example/cb"
        assert body["sign"] == jinke_sign(body, KEY)
        return httpx.Response(200, json={"code": "0", "data": {"payUrl": "https://pay/x"}})

    _patch_client(monkeypatch, "plugins.jinke.plugin.get_http_client", handler)
    plugin = JinkePayment({
        "appurl": "https://jk.example/create", "appid": "10001",
        "appkey": KEY, "productid": "9001",
    })
    res = await plugin.submit(_ctx())
    assert seen["url"] == "https://jk.example/create"
    assert "application/json" in seen["content_type"]
    assert res.kind == "jump" and res.url == "https://pay/x"


async def test_jinke_submit_error_response(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "500", "msg": "商户不存在"})

    _patch_client(monkeypatch, "plugins.jinke.plugin.get_http_client", handler)
    plugin = JinkePayment({"appurl": "https://jk.example/create", "appkey": KEY})
    res = await plugin.submit(_ctx())
    assert res.kind == "error" and "商户不存在" in res.msg


# ============================================================
# RS 支付
# ============================================================

def _rspay_params(**over) -> dict:
    params = {
        "memberid": "10001", "orderid": "T20261128001", "amount": "9.90",
        "datetime": "2026-11-28 12:00:00", "transaction_id": "TX001", "returncode": "00",
    }
    params.update(over)
    params.pop("sign", None)
    # 上游签名只覆盖固定六字段，附加字段不参与
    signed = {k: params[k] for k in (
        "memberid", "orderid", "amount", "datetime", "transaction_id", "returncode")}
    params["sign"] = rspay_sign(signed, KEY)
    return params


async def test_rspay_notify_success():
    plugin = RsPayPayment({"appid": "10001", "appkey": KEY})
    verdict = await plugin.notify_handler(_rspay_params())
    assert verdict.ok is True
    assert verdict.ack == "OK"
    assert verdict.money == Decimal("9.90")
    assert verdict.api_trade_no == "TX001"


async def test_rspay_notify_sign_only_covers_fixed_fields():
    """回调多带字段不参与签名（上游口径），验签仍应通过。"""
    plugin = RsPayPayment({"appkey": KEY})
    verdict = await plugin.notify_handler(_rspay_params(attach="备注", extra="x"))
    assert verdict.ok is True


async def test_rspay_notify_rejects_tampered_amount():
    plugin = RsPayPayment({"appkey": KEY})
    params = _rspay_params()
    params["amount"] = "0.01"
    verdict = await plugin.notify_handler(params)
    assert verdict.ok is False and verdict.ack == "FAIL"


async def test_rspay_notify_non_success_acks_ok():
    plugin = RsPayPayment({"appkey": KEY})
    verdict = await plugin.notify_handler(_rspay_params(returncode="01"))
    assert verdict.ok is False and verdict.ack == "OK"


async def test_rspay_return_handler():
    plugin = RsPayPayment({"appkey": KEY})
    ok = await plugin.return_handler(_rspay_params())
    assert ok["status"] == "paid"
    assert ok["out_trade_no"] == "T20261128001"
    assert ok["money"] == "9.90"
    assert await plugin.return_handler(_rspay_params(returncode="01")) is None


async def test_rspay_submit_form_and_jump(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        seen["body"] = body
        assert body["pay_memberid"] == "10001"
        assert body["pay_bankcode"] == "9001"
        assert body["pay_amount"] == "9.90"
        assert body["pay_orderid"] == "T20261128001"
        assert body["pay_productname"] == "测试商品"
        assert body["pay_md5sign"] == rspay_sign(body, KEY)
        return httpx.Response(200, json={"status": "1", "h5_url": "https://pay/rs"})

    _patch_client(monkeypatch, "plugins.rspay.plugin.get_http_client", handler)
    plugin = RsPayPayment({
        "appurl": "https://rs.example/pay", "appid": "10001",
        "appkey": KEY, "productid": "9001",
    })
    res = await plugin.submit(_ctx())
    assert res.kind == "jump" and res.url == "https://pay/rs"


# ============================================================
# 立鼎支付
# ============================================================

def _liding_params(**over) -> dict:
    params = {
        "mchId": "10001", "mchOrderNo": "T20261128001", "amount": 990,
        "payOrderId": "PO001", "channelOrderNo": "CH001", "status": "2",
    }
    params.update(over)
    params.pop("sign", None)
    params["sign"] = liding_sign(params, KEY)
    return params


async def test_liding_notify_success():
    plugin = LidingPayment({"appid": "10001", "appkey": KEY})
    verdict = await plugin.notify_handler(_liding_params())
    assert verdict.ok is True
    assert verdict.ack == "success"
    assert verdict.money == Decimal("9.90")
    assert verdict.api_trade_no == "CH001"


async def test_liding_notify_status_three_is_paid():
    plugin = LidingPayment({"appkey": KEY})
    verdict = await plugin.notify_handler(_liding_params(status="3"))
    assert verdict.ok is True


async def test_liding_notify_rejects_tampered_amount():
    plugin = LidingPayment({"appkey": KEY})
    params = _liding_params()
    params["amount"] = 1
    verdict = await plugin.notify_handler(params)
    assert verdict.ok is False and verdict.ack == "FAIL"


async def test_liding_notify_non_success_acks_success():
    plugin = LidingPayment({"appkey": KEY})
    verdict = await plugin.notify_handler(_liding_params(status="4"))
    assert verdict.ok is False and verdict.ack == "success"


async def test_liding_return_handler():
    plugin = LidingPayment({"appkey": KEY})
    ok = await plugin.return_handler(_liding_params())
    assert ok["status"] == "paid"
    assert ok["out_trade_no"] == "T20261128001"
    assert ok["money"] == "9.90"
    assert await plugin.return_handler(_liding_params(status="1")) is None


async def test_liding_submit_form_jump(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        seen["body"] = body
        assert body["mchId"] == "10001"
        assert body["productId"] == "9001"
        assert body["amount"] == "990"
        assert body["currency"] == "cny"
        assert body["version"] == "1.0"
        assert len(body["reqTime"]) == 14
        assert body["clientIp"] == "1.2.3.4"
        assert body["sign"] == liding_sign(body, KEY)
        return httpx.Response(200, json={
            "retCode": "0", "payMethod": "formJump", "payJumpUrl": "https://pay/ld",
        })

    _patch_client(monkeypatch, "plugins.liding.plugin.get_http_client", handler)
    plugin = LidingPayment({
        "appurl": "https://ld.example/create", "appid": "10001",
        "appkey": KEY, "productid": "9001",
    })
    res = await plugin.submit(_ctx())
    assert res.kind == "jump" and res.url == "https://pay/ld"


async def test_liding_submit_code_img_paths(monkeypatch):
    plugin = LidingPayment({
        "appurl": "https://ld.example/create", "appid": "10001",
        "appkey": KEY, "productid": "9001",
    })

    def raw_qr(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "retCode": "0", "payMethod": "codeImg", "codeUrl": "weixin://wxpay/bizpayurl?x=1",
        })

    _patch_client(monkeypatch, "plugins.liding.plugin.get_http_client", raw_qr)
    res = await plugin.submit(_ctx())
    assert res.kind == "qrcode" and res.url.startswith("weixin://")

    def image_qr(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "retCode": "0", "payMethod": "codeImg",
            "codeImgUrl": "https://ld.example/qr/abc.png",
        })

    _patch_client(monkeypatch, "plugins.liding.plugin.get_http_client", image_qr)
    res = await plugin.submit(_ctx())
    assert res.kind == "html"
    assert '<img src="https://ld.example/qr/abc.png"' in res.html


async def test_liding_submit_error_response(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"retCode": "1", "retMsg": "签名错误"})

    _patch_client(monkeypatch, "plugins.liding.plugin.get_http_client", handler)
    plugin = LidingPayment({"appurl": "https://ld.example/create", "appkey": KEY})
    res = await plugin.submit(_ctx())
    assert res.kind == "error" and "签名错误" in res.msg


# ============================================================
# 回调路由闭环：金科成功应答必须是 "OK"（不是 success）
# ============================================================

async def test_jinke_callback_route_acks_ok(client, session, seeded, monkeypatch):
    from sqlmodel import select

    from app.models import Order
    from app.services.crypto import encrypt_config
    from tests.conftest import signed_params

    channel = seeded["channel"]
    channel.plugin_name = "jinke"
    channel.config = encrypt_config({
        "appurl": "https://jk.example/create", "appid": "10001",
        "appkey": KEY, "productid": "9001",
    })
    session.add(channel)
    await session.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "0", "data": {"payUrl": "https://pay/x"}})

    _patch_client(monkeypatch, "plugins.jinke.plugin.get_http_client", handler)

    resp = await client.post("/api/orders", json=signed_params({
        "pid": "1001", "type": "alipay", "out_trade_no": "JK-ROUTE-1",
        "notify_url": "https://merchant.example/notify",
        "return_url": "https://merchant.example/return",
        "name": "金科回调路由", "money": "9.90",
    }))
    assert resp.json()["code"] == 0, resp.json()

    order = (await session.exec(
        select(Order).where(Order.out_trade_no == "JK-ROUTE-1"))).one()

    notify = _jinke_params(outTradeNo=order.trade_no, amount=990, tradeNo="JK001")
    resp = await client.post(f"/callbacks/jinke/{order.trade_no}", json=notify)
    assert resp.text == "OK", resp.text

    await session.refresh(order)
    assert order.status == 1
    assert order.api_trade_no == "JK001"

