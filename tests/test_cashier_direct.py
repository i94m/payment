"""随机金额商户收银台直跳测试（FREE-33 需求 1）。

开启了随机金额的商户，收银台支付链接不展示收银台页面：
- 待支付单直接拉起支付（已拉起过则复用落库支付信息）并 302 跳转上游
- qrcode / scheme / html 形态渲染对应展示页
- 拉起失败 / 订单异常渲染统一错误结果页；已支付渲染成功页
- 非随机金额商户仍渲染收银台自选支付方式页面
"""
import pytest

from app.models import Merchant, Order
from app.lib.linktoken import encode_link_token  # noqa: F401
from tests.conftest import signed_params


async def _fetch_order(trade_no: str):
    """从应用使用的同一测试库读取订单。"""
    from app.core.database import get_db
    from main import app as _app
    from app.services.order import get_order_by_trade_no

    override = _app.dependency_overrides[get_db]
    async for s in override():
        return await get_order_by_trade_no(s, trade_no)
    return None


async def _create_order(client, out_trade_no="CD1", money="100.00"):
    """走 POST /api/orders 下单（下单时即拉起并落库支付信息）。"""
    params = {
        "pid": "1001", "type": "alipay", "out_trade_no": out_trade_no,
        "notify_url": "https://merchant.example/notify",
        "return_url": "https://merchant.example/return",
        "name": "直跳测试", "money": money,
    }
    params = signed_params(params)
    resp = await client.post("/api/orders", json=params)
    data = resp.json()
    assert data["code"] == 0, data
    return await _fetch_order(data["data"]["trade_no"])


async def _enable_random(session, seeded, status=1):
    m = await session.get(Merchant, seeded["merchant"].id)
    m.random_enable = True
    m.random_min = 5
    m.random_max = 10
    session.add(m)
    await session.commit()
    return m


async def _update_order(session, order, **kw):
    o = await session.get(Order, order.id)
    for k, v in kw.items():
        setattr(o, k, v)
    session.add(o)
    await session.commit()
    return o


@pytest.mark.asyncio
async def test_direct_redirect_reuses_stored_pay_info(client, seeded, session):
    """已拉起过的待支付单：302 直跳落库的支付地址（不重新请求上游）。"""
    await _enable_random(session, seeded)
    order = await _create_order(client, "CD-REUSE")
    # 落库地址打标：若重新拉起会拿到 mock 上游的 abc123 地址
    marked = "https://upstream.example/pay/MARKED"
    await _update_order(session, order, extra={"pay_type": "jump", "pay_info": marked})

    from app.lib.linktoken import encode_link_token
    resp = await client.get(f"/cashier/{encode_link_token(order.trade_no)}",
                            follow_redirects=False)
    assert resp.status_code == 302, resp.text[:200]
    assert resp.headers["location"] == marked


@pytest.mark.asyncio
async def test_direct_redirect_launches_when_not_pulled(client, seeded, session):
    """未拉起过的待支付单：现场拉起支付并 302 到上游地址。"""
    await _enable_random(session, seeded)
    order = await _create_order(client, "CD-FRESH")
    await _update_order(session, order, extra={})

    from app.lib.linktoken import encode_link_token
    resp = await client.get(f"/cashier/{encode_link_token(order.trade_no)}",
                            follow_redirects=False)
    assert resp.status_code == 302, resp.text[:200]
    assert resp.headers["location"] == "https://upstream.example/pay/abc123"
    fresh = await _fetch_order(order.trade_no)
    assert (fresh.extra or {}).get("pay_type") == "jump"


@pytest.mark.asyncio
async def test_direct_launch_failure_shows_error_page(client, seeded, session):
    """拉起失败（通道下线）：统一错误结果页展示错误信息 + 刷新重试。"""
    from app.models import Channel

    await _enable_random(session, seeded)
    order = await _create_order(client, "CD-FAIL")
    await _update_order(session, order, extra={})
    ch = await session.get(Channel, seeded["channel"].id)
    ch.status = 0
    session.add(ch)
    await session.commit()

    resp = await client.get(f"/cashier/{encode_link_token(order.trade_no)}")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "拉起支付失败" in resp.text
    assert "无可用支付通道" in resp.text
    assert "刷新重试" in resp.text
    assert order.trade_no in resp.text


@pytest.mark.asyncio
async def test_direct_paid_shows_success_page(client, seeded, session):
    """已支付单：成功结果页，附带回跳地址（带签名参数）。"""
    await _enable_random(session, seeded)
    order = await _create_order(client, "CD-PAID")
    await _update_order(session, order, status=1)

    resp = await client.get(f"/cashier/{encode_link_token(order.trade_no)}")
    assert resp.status_code == 200
    assert "支付成功" in resp.text
    # return_url 携带签名参数回跳
    assert "https://merchant.example/return" in resp.text


@pytest.mark.asyncio
async def test_direct_expired_shows_error_page(client, seeded, session):
    """过期/异常状态单：错误结果页。"""
    await _enable_random(session, seeded)
    order = await _create_order(client, "CD-EXP")
    await _update_order(session, order, status=3)

    resp = await client.get(f"/cashier/{encode_link_token(order.trade_no)}")
    assert resp.status_code == 200
    assert "订单无法支付" in resp.text
    assert "过期" in resp.text


@pytest.mark.asyncio
async def test_direct_qrcode_renders_qr_page(client, seeded, session):
    """qrcode 形态：扫码页内联渲染二维码 SVG + 实付金额。"""
    await _enable_random(session, seeded)
    order = await _create_order(client, "CD-QR")
    await _update_order(
        session, order,
        extra={"pay_type": "qrcode", "pay_info": "https://qr.example/pay/x"},
        realmoney=8,
    )

    resp = await client.get(f"/cashier/{encode_link_token(order.trade_no)}")
    assert resp.status_code == 200
    assert "扫码支付" in resp.text
    assert "<svg" in resp.text and "path" in resp.text
    assert "8.00" in resp.text  # 展示实付金额（realmoney）
    assert order.trade_no in resp.text


@pytest.mark.asyncio
async def test_direct_scheme_renders_launcher(client, seeded, session):
    """scheme 形态：拉起页自动 location.href，2.5s 后展示手动按钮。"""
    await _enable_random(session, seeded)
    order = await _create_order(client, "CD-SCHEME")
    await _update_order(
        session, order,
        extra={"pay_type": "scheme", "pay_info": "alipays://platformapi/startapp?appId=200"},
    )

    resp = await client.get(f"/cashier/{encode_link_token(order.trade_no)}")
    assert resp.status_code == 200
    assert "正在拉起支付" in resp.text
    assert "alipays://platformapi/startapp?appId=200" in resp.text


@pytest.mark.asyncio
async def test_direct_html_rendered_verbatim(client, seeded, session):
    """html 形态：上游返回的表单 HTML 原样渲染。"""
    await _enable_random(session, seeded)
    order = await _create_order(client, "CD-HTML")
    upstream_html = ('<!DOCTYPE html><html><body><form id="f" action="https://up.example/pay">'
                     '<input type="submit"></form><script>document.getElementById("f").submit()</script>'
                     "</body></html>")
    await _update_order(session, order, extra={"pay_type": "html", "pay_info": upstream_html})

    resp = await client.get(f"/cashier/{encode_link_token(order.trade_no)}")
    assert resp.status_code == 200
    assert "up.example/pay" in resp.text
    assert "document.getElementById" in resp.text


@pytest.mark.asyncio
async def test_non_random_merchant_gets_cashier_page(client, seeded):
    """非随机金额商户：仍渲染收银台自选支付方式页面。"""
    order = await _create_order(client, "CD-NORMAL")
    resp = await client.get(f"/cashier/{encode_link_token(order.trade_no)}")
    assert resp.status_code == 200
    assert "收银台" in resp.text
    assert "立即支付" in resp.text


@pytest.mark.asyncio
async def test_unknown_trade_no_gets_cashier_page(client, seeded):
    """订单不存在：错误结果页（订单不存在或已失效）。"""
    resp = await client.get(f"/cashier/{encode_link_token('NOPE')}")
    assert resp.status_code == 200
    assert "订单不存在" in resp.text


@pytest.mark.asyncio
async def test_paypage_random_flow_redirects_to_upstream(client, seeded, session):
    """端到端：随机金额收款页建单 → cashier_url → 302 直跳上游（不展示收银台）。"""
    await _enable_random(session, seeded)
    from tests.conftest import pay_code
    resp = await client.post("/paypage/order",
                             json={"token": await pay_code(session), "money": ""})
    data = resp.json()
    assert data["code"] == 0, data
    cashier_url = data["data"]["cashier_url"].replace("http://testserver", "")

    resp2 = await client.get(cashier_url, follow_redirects=False)
    assert resp2.status_code == 302, resp2.text[:300]
    assert resp2.headers["location"].startswith("https://upstream.example/pay/")


@pytest.mark.asyncio
async def test_paypage_random_auto_redirect_full_chain(client, seeded, session):
    """随机金额收款页 GET 全链路（对齐旧版 paypage/index.php）：

    首访渲染倒计时页（JS + meta refresh 自动跳转，无“生成付款订单”按钮）
    → go=1 服务端建随机金额单并 302 到收银台链接
    → 收银台链接 302 直跳上游（真实拉起，不展示收银台页面）。
    """
    from sqlmodel import col, select

    from tests.conftest import pay_code
    await _enable_random(session, seeded)
    code = await pay_code(session)

    resp = await client.get(f"/p/{code}")
    assert resp.status_code == 200
    assert f"/p/{code}?go=1" in resp.text            # meta refresh / JS 跳转地址
    assert 'http-equiv="refresh"' in resp.text       # meta 兜底
    assert "location.replace" in resp.text           # JS 主跳转
    assert "生成付款订单" not in resp.text            # 不再渲染表单按钮

    resp2 = await client.get(f"/p/{code}", params={"go": "1"}, follow_redirects=False)
    assert resp2.status_code == 302, resp2.text[:300]
    assert "/cashier/" in resp2.headers["location"]

    resp3 = await client.get(resp2.headers["location"].replace("http://testserver", ""),
                             follow_redirects=False)
    assert resp3.status_code == 302, resp3.text[:300]
    assert resp3.headers["location"].startswith("https://upstream.example/pay/")

    # 建单金额落在随机区间 [5,10]
    latest = (await session.exec(
        select(Order).where(Order.merchant_id == seeded["merchant"].id)
        .order_by(col(Order.id).desc())
    )).first()
    assert latest is not None and latest.out_trade_no.startswith("PP")
    assert 5 <= float(latest.money) <= 10


@pytest.mark.asyncio
async def test_paypage_non_random_merchant_shows_form(client, seeded, session):
    """普通商户收款页：仍渲染输金额表单（无自动跳转）。"""
    from tests.conftest import pay_code
    code = await pay_code(session)
    resp = await client.get(f"/p/{code}")
    assert resp.status_code == 200
    assert "pay-btn" in resp.text
    assert "go=1" not in resp.text


@pytest.mark.asyncio
async def test_paypage_invalid_token_shows_error_page(client, seeded):
    """短码无效：收款页门禁错误页。"""
    resp = await client.get("/p/NOTACODE1X")
    assert resp.status_code == 200
    assert "无法付款" in resp.text
    assert "链接无效" in resp.text


@pytest.mark.asyncio
async def test_paypage_random_pay_disabled_shows_error_page(client, seeded, session):
    """随机金额商户支付权限关闭：收款页门禁错误页（不建单）。"""
    from tests.conftest import pay_code
    m = await _enable_random(session, seeded)
    m.pay_status = 0
    session.add(m)
    await session.commit()
    code = await pay_code(session)
    resp = await client.get(f"/p/{code}")
    assert resp.status_code == 200
    assert "无法付款" in resp.text
    assert "商户暂不可收款" in resp.text
