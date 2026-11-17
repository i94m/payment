"""Playwright 端到端冒烟测试（对运行中的本地服务，适配 React + shadcn/ui 前端）。

前置：
- 平台 http://127.0.0.1:8000（APP_ENV=local，万能验证码 0000）
- 模拟上游 http://127.0.0.1:9000
- 种子：admin/123456，demo/demo123456（pid=1001），epay 通道
- 前端产物 static/console 已构建（现场构建，不入库）；/docs 为独立 VitePress 构建，
  未构建时登录后访问接入文档会 404，故本套件不对其内容做断言

运行（playwright 在可选组 e2e 里，默认 `uv sync` 不装；首次需拉浏览器）：
    uv sync --group e2e && uv run playwright install chromium
    uv run --group e2e python scripts/e2e_smoke.py
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _demo_key import DEMO_MERCHANT_PRIVATE_KEY
from app.lib.sign import make_rsa_sign

BASE = "http://127.0.0.1:8000"
MOCK = "http://127.0.0.1:9000"

PASS = []
FAIL = []


def check(name: str, fn):
    try:
        fn()
        PASS.append(name)
        print(f"  [PASS] {name}")
    except Exception as e:
        FAIL.append((name, str(e)[:300]))
        print(f"  [FAIL] {name}: {str(e)[:300]}")


def api_create_order(money="6.00") -> dict:
    out = "E2E" + time.strftime("%H%M%S")
    params = {
        "pid": "1001", "type": "alipay", "out_trade_no": out,
        "notify_url": "https://merchant.example/notify",
        "return_url": BASE + "/", "name": "E2E商品", "money": money,
    }
    params["timestamp"] = str(int(time.time()))
    params["sign"] = make_rsa_sign(params, DEMO_MERCHANT_PRIVATE_KEY)
    params["sign_type"] = "RSA2"
    req = urllib.request.Request(BASE + "/api/orders", data=json.dumps(params).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        body = json.loads(r.read())
    assert body["code"] == 0, body
    return body["data"]


def api_get(path: str, token: str | None = None) -> dict:
    req = urllib.request.Request(BASE + path)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def api_post(path: str, payload: dict, token: str | None = None) -> dict:
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def ui_login(page, entry: str, username: str, password: str):
    """在指定端完成登录（entry 如 "/admin" 总后台、"/" 商户中心），等待离开登录页。"""
    page.goto(BASE + entry, wait_until="networkidle")
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)
    page.fill('input[name="captchaCode"]', "0000")
    page.click('button[type="submit"]')
    page.wait_for_url(lambda url: "#/login" not in url, timeout=15000)
    # 登录成功处理器还会把路由推回首页；等它完成，否则紧随的 goto 目标路由会被覆盖
    page.wait_for_timeout(1500)


def wait_hash_page(page, timeout: int = 10000):
    """hash 路由切换后等待页面数据渲染完成（SPA 请求落定 + 表格/按钮出现）。"""
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(800)


def main():
    with sync_playwright() as p:

        try:
            browser = p.chromium.launch(headless=True, channel="chrome")
        except Exception:
            browser = p.chromium.launch(headless=True)

        # ============ 场景 1：管理后台 ============
        print("== 场景1 管理后台")
        ctx = browser.new_context()
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        check("admin 登录（验证码 0000）",
              lambda: ui_login(page, "/admin", "admin", "123456"))

        def admin_dashboard():
            expect(page.locator("text=今日订单").first).to_be_visible(timeout=10000)

        check("admin 看板统计渲染", admin_dashboard)

        for path, marker in [
            ("/trade/orders", "平台订单号"),
            ("/trade/notifylogs", "尝试次数"),
            ("/merchants", "商户密钥"),
            ("/pay/channels", "商户费率"),
            ("/pay/plugins", "同步插件"),
            ("/pay/types", "新增支付方式"),
            ("/settle", "结算单号"),
            ("/announces", "新建公告"),
            ("/setting/pay", "保存设置"),
            ("/system/accounts", "新增账号"),
        ]:
            def nav(path=path, marker=marker):
                page.goto(BASE + "/admin#" + path, wait_until="networkidle")
                wait_hash_page(page)
                expect(page.locator(f"text={marker}").first).to_be_visible(timeout=10000)
            check(f"admin 页面 {path}", nav)

        # 商户创建（走 UI 弹窗）
        def create_merchant():
            page.goto(BASE + "/admin#/merchants", wait_until="networkidle")
            wait_hash_page(page)
            page.click("button:has-text('新增商户')")
            modal = page.locator("[role='dialog']")
            expect(modal).to_be_visible(timeout=8000)
            uname = "e2e" + time.strftime("%H%M%S")
            modal.locator("input[placeholder='用户名']").fill(uname)
            modal.locator("input[type='password']").fill("e2e123456")
            modal.locator("button:has-text('保存')").click()
            expect(modal).to_be_hidden(timeout=8000)
            page.fill("input[placeholder='搜索内容']", uname)
            page.click("button:has-text('查询')")
            wait_hash_page(page)
            expect(page.locator("table tbody tr", has_text=uname).first).to_be_visible(timeout=8000)
        check("admin UI 创建商户并检索到", create_merchant)

        check("admin 全程无 JS 错误", lambda: (_ for _ in ()).throw(AssertionError(errors)) if errors else None)
        ctx.close()

        # ============ 场景 2：商户中心 ============
        print("== 场景2 商户中心")
        ctx2 = browser.new_context()
        page2 = ctx2.new_page()
        errors2 = []
        page2.on("pageerror", lambda e: errors2.append(str(e)))

        check("merchant 登录", lambda: ui_login(page2, "/", "demo", "demo123456"))

        for path, marker in [
            ("/api/orders", "平台订单号"),
            ("/settle", "可结算余额"),
            ("/records", "资金流水"),
            ("/security", "接口密钥"),
            ("/user-center", "收款账号设置"),
        ]:
            def nav2(path=path, marker=marker):
                page2.goto(BASE + "/#" + path, wait_until="networkidle")
                wait_hash_page(page2)
                expect(page2.locator(f"text={marker}").first).to_be_visible(timeout=10000)
            check(f"merchant 页面 {path}", nav2)

        check("merchant 全程无 JS 错误", lambda: (_ for _ in ()).throw(AssertionError(errors2)) if errors2 else None)
        ctx2.close()

        # ============ 场景 3：浏览器完整支付闭环 ============
        print("== 场景3 收银台支付闭环")
        ctx3 = browser.new_context()
        page3 = ctx3.new_page()
        order = api_create_order("6.66")
        trade_no = order["trade_no"]

        def cashier_pay():
            page3.goto(order["cashier_url"], wait_until="networkidle")
            expect(page3.locator(".type-item").first).to_be_visible(timeout=10000)
            page3.locator(".type-item").first.click()
            page3.click("#pay-btn")
            page3.wait_for_url("**/127.0.0.1:9000/**", timeout=20000)
            page3.click("text=模拟支付成功")
            page3.wait_for_url("**/cashier**", timeout=20000)
            expect(page3.locator("text=支付成功").first).to_be_visible(timeout=25000)

        check("收银台 → 模拟上游 → 支付成功", cashier_pay)

        def paid_state():
            body = api_get(f"/cashier/status?trade_no={trade_no}")
            assert body["data"]["status"] == 1, body
        check("订单状态已落库为已支付", paid_state)
        ctx3.close()

        # ============ 场景 4：商户 UI 查到该订单 ============
        ctx4 = browser.new_context()
        page4 = ctx4.new_page()

        def merchant_sees_order():
            ui_login(page4, "/", "demo", "demo123456")
            page4.goto(BASE + "/#/orders", wait_until="networkidle")
            wait_hash_page(page4)
            expect(page4.locator(f"text={trade_no}").first).to_be_visible(timeout=10000)
        check("merchant UI 查询到 E2E 订单", merchant_sees_order)
        ctx4.close()

        browser.close()

    print(f"\n==== 结果: {len(PASS)} 通过, {len(FAIL)} 失败 ====")
    for name, err in FAIL:
        print(f"  [FAIL] {name}: {err}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
