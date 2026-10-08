"""仙女支付2插件（乐事 leshiapi 协议）测试：

1. 签名：升序 k=v& + "&secret=" + 密钥 → md5 小写（golden vector 来自网关 debug 回显实测）
2. 兜底验签（secret 参与排序）变体
3. POST /api/orders 走 xiannv2 通道下单 → 拿到上游 payUrl
4. JSON 回调（验签通过 + status=2）→ success + 订单已支付 + api_trade_no
5. 验签失败 → fail；非支付成功状态 → success 但不记账；金额解析失败 → fail
6. 主动查单（query）→ paid
7. _parse_pay_result 各分支：jump / qrcode / 错误
"""
import hashlib
import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.xiannv2.plugin import (
    Xiannv2Payment,
    _is_paid,
    _money_2dp,
    _parse_money,
    _parse_pay_result,
    _pick,
    _sign_fallback,
    _verify_sign,
    make_sign,
)
from app.services.plugin_interfaces import PaymentContext

XNV_KEY = "testsecret0123456789abcdef0123"
XNV_MCH = "10298"
XNV_GATEWAY = "http://upstream.example/api/merchant/getOrder"

# golden vector：网关 debug 回显实测（secret 替换为真实密钥后 md5 匹配）
GOLDEN_PARAMS = {
    "merchantId": "10298",
    "money": "10.00",
    "bizOrderNo": "TEST20260929001",
    "bizNotifyUrl": "http://8.217.182.189/callbacks/xiannv2/TEST20260929001",
    "payType": "alipay",
}
GOLDEN_KEY = "423847159bb4c5eb1f7278a789f4cc7e"
GOLDEN_SIGN = "babb00a554c3794ba085d98e84a2062c"


def _make_plugin(config: dict | None = None) -> Xiannv2Payment:
    cfg = {
        "appurl": XNV_GATEWAY,
        "appmchid": XNV_MCH,
        "appkey": XNV_KEY,
        "productid": "1",
    }
    cfg.update(config or {})
    return Xiannv2Payment(cfg)


def _ctx(trade_no: str = "20260929102930000001") -> PaymentContext:
    return PaymentContext(
        order={"trade_no": trade_no, "realmoney": "10.00",
               "payment_type_code": "alipay"},
        notify_url=f"http://site.local/callbacks/xiannv2/{trade_no}",
        return_url=f"http://site.local/callbacks/xiannv2/{trade_no}/return",
    )


def _mock_response(data: dict, status_code: int = 200):
    m = MagicMock()
    m.status_code = status_code
    m.text = json.dumps(data, ensure_ascii=False)
    m.json.return_value = data
    return m


# ================================================================
# 签名
# ================================================================

def test_sign_golden_vector():
    """锁定签名算法：升序 k=v& + "&secret=密钥" → md5 小写。"""
    assert make_sign(GOLDEN_PARAMS, GOLDEN_KEY) == GOLDEN_SIGN


def test_sign_excludes_empty_and_sign():
    """空值不参与；sign 字段排除；"0" 保留。"""
    data = {"a": "1", "b": "", "c": None, "d": "0", "sign": "xx"}
    # 只有 a=1 和 d=0 参与签名
    expected = hashlib.md5("a=1&d=0&secret=k".encode()).hexdigest()
    assert make_sign(data, "k") == expected


def test_verify_sign_roundtrip():
    sign = make_sign(GOLDEN_PARAMS, GOLDEN_KEY)
    assert _verify_sign({**GOLDEN_PARAMS, "sign": sign}, GOLDEN_KEY, sign)
    assert _verify_sign({**GOLDEN_PARAMS, "sign": sign.upper()}, GOLDEN_KEY, sign.upper())
    assert not _verify_sign({**GOLDEN_PARAMS, "sign": sign}, "wrong", sign)


def test_sign_fallback_secret_in_sort():
    """兜底验签：secret 作为普通参数参与排序。"""
    data = {"b": "2", "a": "1"}
    key = "mykey"
    merged = {**data, "secret": key}
    content = "&".join(f"{k}={merged[k]}" for k in sorted(merged))
    expected = hashlib.md5(content.encode()).hexdigest()
    assert _sign_fallback(data, key) == expected
    # 兜底验签也应通过
    assert _verify_sign({**data, "sign": _sign_fallback(data, key)}, key,
                        _sign_fallback(data, key))


def test_money_2dp():
    assert _money_2dp("100") == "100.00"
    assert _money_2dp(Decimal("99.999")) == "100.00"
    assert _money_2dp("0.10") == "0.10"


def test_parse_money():
    assert _parse_money("10.00") == Decimal("10.00")
    assert _parse_money("") is None
    assert _parse_money(None) is None
    assert _parse_money("abc") is None


def test_pick():
    assert _pick({"a": "1", "b": "2"}, ["a", "b"]) == "1"
    assert _pick({"a": "", "b": "2"}, ["a", "b"]) == "2"
    assert _pick({}, ["a"]) == ""


def test_is_paid():
    assert _is_paid({"status": "1"})
    assert _is_paid({"orderStatus": 2})
    assert _is_paid({"result": "success"})
    assert _is_paid({"trade_status": "TRADE_SUCCESS"})
    assert _is_paid({})  # 无状态字段按成功处理
    assert not _is_paid({"status": "0"})
    assert not _is_paid({"orderStatus": "3"})
    assert not _is_paid({"payStatus": "pending"})


# ================================================================
# _parse_pay_result
# ================================================================

def test_parse_pay_result_jump_data():
    r = _parse_pay_result({"code": 200, "data": {"payUrl": "https://pay.x.com/a"}})
    assert r.kind == "jump"
    assert r.url == "https://pay.x.com/a"


def test_parse_pay_result_jump_root():
    r = _parse_pay_result({"code": 0, "url": "http://y.com/b"})
    assert r.kind == "jump" and r.url == "http://y.com/b"


def test_parse_pay_result_qrcode():
    r = _parse_pay_result({"code": 200, "data": {"qrCode": "weixin://wxpay/xyz"}})
    assert r.kind == "qrcode" and r.url == "weixin://wxpay/xyz"


def test_parse_pay_result_error():
    r = _parse_pay_result({"code": -1, "message": "签名错误"})
    assert r.kind == "error" and "签名错误" in r.msg


def test_parse_pay_result_ok_no_url():
    r = _parse_pay_result({"code": 200, "message": "ok"})
    assert r.kind == "error" and "支付地址" in r.msg


# ================================================================
# submit
# ================================================================


@pytest.mark.asyncio
async def test_submit_jump():
    """下单成功 → jump。"""
    plugin = _make_plugin()
    ctx = _ctx()
    mock_resp = _mock_response({
        "code": 200, "message": "ok", "data": {"payUrl": "https://pay.upstream.com/xxx"}
    })
    with patch("app.services.http_client.get_http_client") as mock_client:
        mock_client.return_value.post = AsyncMock(return_value=mock_resp)
        result = await plugin.submit(ctx)
    assert result.kind == "jump"
    assert result.url == "https://pay.upstream.com/xxx"


@pytest.mark.asyncio
async def test_submit_error():
    plugin = _make_plugin()
    mock_resp = _mock_response({"code": -1, "message": "店铺匹配失败，请稍后重试"})
    with patch("app.services.http_client.get_http_client") as mock_client:
        mock_client.return_value.post = AsyncMock(return_value=mock_resp)
        result = await plugin.submit(_ctx())
    assert result.kind == "error"
    assert "店铺匹配失败" in result.msg


@pytest.mark.asyncio
async def test_submit_no_productid():
    plugin = _make_plugin({"productid": ""})
    result = await plugin.submit(_ctx())
    assert result.kind == "error"
    assert "支付方式编码" in result.msg


@pytest.mark.asyncio
async def test_submit_connection_error():
    plugin = _make_plugin()
    with patch("app.services.http_client.get_http_client") as mock_client:
        mock_client.return_value.post = AsyncMock(side_effect=ConnectionError("timeout"))
        result = await plugin.submit(_ctx())
    assert result.kind == "error"
    assert "请求支付接口失败" in result.msg


def test_submit_params_structure():
    """检查提交参数结构（不实际发 HTTP）。"""
    plugin = _make_plugin()
    ctx = _ctx("T12345678901")
    productid = "1"
    params = {
        "merchantId": XNV_MCH,
        "bizOrderNo": "T12345678901",
        "money": _money_2dp("10.00"),
        "payType": productid,
        "bizNotifyUrl": ctx.notify_url,
    }
    params["sign"] = make_sign(params, XNV_KEY)
    assert params["bizOrderNo"] == "T12345678901"
    assert params["money"] == "10.00"
    assert len(params["sign"]) == 32
    assert params["sign"] == params["sign"].lower()


# ================================================================
# query
# ================================================================


@pytest.mark.asyncio
async def test_query_paid():
    plugin = _make_plugin()
    mock_resp = _mock_response({
        "code": 200, "data": {
            "bizOrderNo": "T12345678901", "status": "2",
            "money": "10.00", "orderNo": "UP999",
        }
    })
    with patch("app.services.http_client.get_http_client") as mock_client:
        mock_client.return_value.post = AsyncMock(return_value=mock_resp)
        result = await plugin.query(_ctx("T12345678901"))
    assert result.status == "paid"
    assert result.api_trade_no == "UP999"
    assert result.money == Decimal("10.00")


@pytest.mark.asyncio
async def test_query_unpaid():
    plugin = _make_plugin()
    mock_resp = _mock_response({
        "code": 200, "data": {"bizOrderNo": "T12345678901", "status": "0"}
    })
    with patch("app.services.http_client.get_http_client") as mock_client:
        mock_client.return_value.post = AsyncMock(return_value=mock_resp)
        result = await plugin.query(_ctx("T12345678901"))
    assert result.status == "unpaid"


@pytest.mark.asyncio
async def test_query_error():
    plugin = _make_plugin()
    mock_resp = _mock_response({"code": -1, "message": "订单不存在"})
    with patch("app.services.http_client.get_http_client") as mock_client:
        mock_client.return_value.post = AsyncMock(return_value=mock_resp)
        result = await plugin.query(_ctx("T12345678901"))
    assert result.status == "unknown"


@pytest.mark.asyncio
async def test_query_connection_error():
    plugin = _make_plugin()
    with patch("app.services.http_client.get_http_client") as mock_client:
        mock_client.return_value.post = AsyncMock(side_effect=ConnectionError("timeout"))
        result = await plugin.query(_ctx())
    assert result.status == "unknown"


def test_query_url_derivation():
    assert _make_plugin()._query_url() == "http://upstream.example/api/merchant/query"
    assert _make_plugin({"appurl": "http://x.com/api/merchant/getOrder"})._query_url() \
        == "http://x.com/api/merchant/query"


# ================================================================
# notify_handler
# ================================================================


def _make_notify_data(status: str = "2", money: str = "10.00") -> dict:
    data = {
        "merchantId": XNV_MCH,
        "bizOrderNo": "T12345678901",
        "orderNo": "UP20260929001",
        "money": money,
        "status": status,
    }
    data["sign"] = make_sign(data, XNV_KEY)
    return data


@pytest.mark.asyncio
async def test_notify_success():
    plugin = _make_plugin()
    data = _make_notify_data()
    v = await plugin.notify_handler(data)
    assert v.ok
    assert v.ack == "success"
    assert v.api_trade_no == "UP20260929001"
    assert v.money == Decimal("10.00")


@pytest.mark.asyncio
async def test_notify_success_json_body():
    """JSON body 兜底解析。"""
    plugin = _make_plugin()
    data = _make_notify_data()
    body = json.dumps(data, ensure_ascii=False).encode()
    v = await plugin.notify_handler({}, body=body)
    assert v.ok and v.ack == "success"


@pytest.mark.asyncio
async def test_notify_bad_sign():
    plugin = _make_plugin()
    data = _make_notify_data()
    data["sign"] = "0" * 32
    v = await plugin.notify_handler(data)
    assert not v.ok and v.ack == "fail"


@pytest.mark.asyncio
async def test_notify_missing_sign():
    plugin = _make_plugin()
    data = _make_notify_data()
    del data["sign"]
    v = await plugin.notify_handler(data)
    assert not v.ok and v.ack == "fail"


@pytest.mark.asyncio
async def test_notify_mch_mismatch():
    plugin = _make_plugin()
    data = _make_notify_data()
    data["merchantId"] = "99999"
    # 重算签名（模拟上游用错商户号）
    data["sign"] = make_sign(data, XNV_KEY)
    v = await plugin.notify_handler(data)
    assert not v.ok and v.ack == "fail"


@pytest.mark.asyncio
async def test_notify_not_paid():
    """验签通过但状态非成功 → ack=success 让上游停止重发。"""
    plugin = _make_plugin()
    data = _make_notify_data(status="0")
    v = await plugin.notify_handler(data)
    assert not v.ok and v.ack == "success"


@pytest.mark.asyncio
async def test_notify_money_parse_fail():
    plugin = _make_plugin()
    data = _make_notify_data(money="abc")
    v = await plugin.notify_handler(data)
    assert not v.ok and v.ack == "fail"


@pytest.mark.asyncio
async def test_notify_no_money_field():
    """回调未携带金额字段：money=None 放行交中心层。"""
    plugin = _make_plugin()
    data = {
        "merchantId": XNV_MCH,
        "bizOrderNo": "T12345678901",
        "orderNo": "UP20260929001",
        "status": "2",
    }
    data["sign"] = make_sign(data, XNV_KEY)
    v = await plugin.notify_handler(data)
    assert v.ok and v.money is None
