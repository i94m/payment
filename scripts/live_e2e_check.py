"""FREE-33 全流程实测脚本（对运行中的本地实例 :8000 + 模拟上游 :9000）。

覆盖：
A. 下游接口：RSA2 签名下单 / 查单 / 幂等
B. 收银台：普通商户收银台页、状态轮询
C. 上游接口：跳上游付款页 → 模拟支付成功回调 → 记账 / 通知排队
D. 随机金额商户：收款页建单 → cashier 链接 302 直跳上游（不展示收银台）
E. API支付开关：关闭后下单拒绝 + 菜单隐藏 + 页面保留 + 收款页不受影响
F. 退款：部分退款 + 剩余全额退款 + 订单状态/余额/流水核对
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _demo_key import DEMO_MERCHANT_PRIVATE_KEY
from app.lib.sign import make_rsa_sign

BASE = os.environ.get("E2E_BASE", "http://127.0.0.1:8000")
MOCK = os.environ.get("E2E_MOCK", "http://127.0.0.1:9000")
PID = "1001"

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = ""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail and not ok else ""))


def signed_params(params: dict) -> dict:
    p = dict(params)
    p["timestamp"] = str(int(time.time()))
    p["sign"] = make_rsa_sign(p, DEMO_MERCHANT_PRIVATE_KEY)
    p["sign_type"] = "RSA2"
    return p


def req(method: str, path: str, body: dict | None = None, params: dict | None = None,
        headers: dict | None = None):
    url = BASE + path
    if params:
        # base64 签名含 +/=，必须 urlencode（否则服务端解码出空格导致验签失败）
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method,
                               headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, json.loads(resp.read()), dict(resp.headers)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read()), dict(e.headers)
        except Exception:
            return e.code, {}, dict(e.headers)


def get_raw(path: str, headers: dict | None = None, follow=True):
    """跟随 302 后返回 (最终URL, 状态, 文本)；不跟随时返回 (原URL, 状态, Location)。"""
    import httpx

    url = path if path.startswith("http") else BASE + path
    with httpx.Client(follow_redirects=follow) as c:
        r = c.get(url, headers=headers or {})
        if not follow and r.status_code in (301, 302, 303, 307, 308):
            return url, r.status_code, r.headers.get("location", "")
        return str(r.url), r.status_code, r.text


def create_order(out_trade_no: str, money: str = "10.00", sign_val: str | None = None):
    params = signed_params({
        "pid": PID, "type": "alipay", "out_trade_no": out_trade_no,
        "notify_url": "https://merchant.example/notify",
        "return_url": BASE + "/landing",
        "name": "全流程实测", "money": money,
    })
    if sign_val is not None:
        params["sign"] = sign_val
    return req("POST", "/api/orders", params)


def db_exec(sql: str, params: tuple = ()):
    """直连测试库。默认本地 sqlite（uvicorn 直跑）；三种后端：
    - E2E_MYSQL="mysql -u root -N --batch pay_test"：宿主机 MySQL（如 brew 服务）
    - E2E_COMPOSE_FILE=xx.yml：docker compose 内的 mysql 服务
    - 其余：本地 sqlite 文件
    """
    import os
    import shlex
    import subprocess

    for p in params:
        sql = sql.replace("?", repr(p) if isinstance(p, (int, float)) else f"'{p}'", 1)

    def _parse_rows(out: str) -> list[tuple]:
        rows = []
        for line in out.strip().splitlines():
            vals = []
            for tok in line.split("\t"):
                if tok == "NULL":
                    vals.append(None)
                else:
                    try:
                        vals.append(int(tok))
                    except ValueError:
                        try:
                            vals.append(float(tok))
                        except ValueError:
                            vals.append(tok)
            rows.append(tuple(vals))
        return rows

    mysql_cmd = os.environ.get("E2E_MYSQL")
    if mysql_cmd:
        out = subprocess.run([*shlex.split(mysql_cmd), "-e", sql],
                             capture_output=True, text=True, check=True).stdout
        return _parse_rows(out)

    compose = os.environ.get("E2E_COMPOSE_FILE")
    if compose:
        out = subprocess.run(
            ["docker", "compose", "-f", compose, "exec", "-T",
             "-e", "MYSQL_PWD=pay_test_123", "mysql",
             "mysql", "-u", "pay_test", "-N", "--batch", "-e", sql, "pay_test"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return _parse_rows(out)

    import sqlite3
    conn = sqlite3.connect("/tmp/pay_live_test.db")
    # 参数已由上方 replace 内联（与 MySQL CLI 路径共用逻辑），不再传 bindings
    cur = conn.execute(sql)
    rows = cur.fetchall()
    conn.commit()
    conn.close()
    return rows


def admin_login() -> dict:
    st, body, _ = req("POST", "/admin-api/auth/login",
                      {"username": "admin", "password": "123456",
                       "captcha_code": "0000", "captcha_key": "local"})
    assert body["code"] == 0, body
    return {"Authorization": f"Bearer {body['data']['access_token']}"}


def merchant_login() -> dict:
    st, body, _ = req("POST", "/merchant-api/auth/login",
                      {"username": "demo", "password": "demo123456",
                       "captcha_code": "0000", "captcha_key": "local"})
    assert body["code"] == 0, body
    return {"Authorization": f"Bearer {body['data']['access_token']}"}


def paypage_token() -> str:
    """商户收款页短码（/p/{code} 不透明链接，替代旧 pid 直传形态）"""
    mh = merchant_login()
    _, body, _ = req("GET", "/merchant-api/paypage/settings", headers=mh)
    link = body["data"]["link"]
    return link.rsplit("/p/", 1)[-1]


def set_setting(headers: dict, key: str, value):
    st, body, _ = req("PATCH", "/admin-api/setting", {key: value}, headers=headers)
    assert body["code"] == 0, (key, value, body)


print("== A. 下游开放接口：签名下单 / 幂等 / 查单 ==")
# 自包含前置：清理上次运行遗留的商户随机金额/开关状态
db_exec("UPDATE merchants SET random_enable=0, random_min=0, random_max=0 WHERE pid=1001")
db_exec("UPDATE settings SET value='true' WHERE `key`='api_pay_enable'")
db_exec("DELETE FROM settings WHERE `key`='site_url'")  # 回退 site_url，用请求地址兜底
tn = f"LIVE{int(time.time())}"
st, body, _ = create_order(tn, "10.00")
check("RSA2签名下单成功", body.get("code") == 0 and body["data"].get("payurl"), str(body)[:200])
trade_no = body["data"]["trade_no"]
payurl = body["data"]["payurl"]
check("返回收银台链接（不透明形态）", "/c/" in body["data"]["cashier_url"] and trade_no not in body["data"]["cashier_url"])

st2, body2, _ = create_order(tn, "10.00")
check("同 out_trade_no 幂等返回原单", body2["data"]["trade_no"] == trade_no, str(body2)[:200])

st3, body3, _ = create_order(tn, "10.00", sign_val="bad")
check("错签拒绝", body3.get("code") != 0 and "签名" in body3.get("message", ""), str(body3)[:200])

q = signed_params({"pid": PID})
st4, body4, _ = req("GET", f"/api/orders/{trade_no}", params=q)
check("查单待支付", body4.get("code") == 0 and body4["data"]["status"] == 0, str(body4)[:200])

print("== B. 收银台页面（普通商户，不透明链接） ==")
_, status, page = get_raw(body["data"]["cashier_url"].replace(BASE, ""))
check("收银台页渲染", status == 200 and "收银台" in page and "立即支付" in page)
st5, body5, _ = req("GET", "/cashier/types", params={"trade_no": trade_no})
check("收银台订单信息含自动跳转信息", body5["code"] == 0 and body5["data"].get("pay_type") == "jump")

print("== C. 上游链路：付款页 → 回调 → 记账/通知 ==")
# mock 上游付款页「模拟支付成功」按钮 → GET /mock-pay 触发对平台回调
import re
import urllib.parse as _up
m = re.search(r"/paypage/([\w]+)\?(.*)", payurl)
up_trade_no, pay_qs = (m.group(1), dict(_up.parse_qsl(m.group(2)))) if m else ("", {})
with urllib.request.urlopen(
        f"{MOCK}/mock-pay?" + _up.urlencode({
            "out": up_trade_no,
            "money": pay_qs.get("money", "10"),
            "type": pay_qs.get("type", "alipay"),
        })) as resp:
    pay_ack = resp.read().decode()
time.sleep(1.5)  # 等回调与记账落库
q2 = signed_params({"pid": PID})
st7, body7, _ = req("GET", f"/api/orders/{trade_no}", params=q2)
check("回调后查单已支付", body7["data"]["status"] == 1, str(body7)[:300])
rows = db_exec("SELECT money, realmoney, merchant_income, status, notify_status FROM orders WHERE trade_no=?",
               (trade_no,))
check("订单记账字段完整（到账=实付*98%）",
      rows and rows[0][3] == 1 and abs(float(rows[0][2]) - float(rows[0][1]) * 0.98) < 0.01, str(rows))
nrows = db_exec("SELECT COUNT(*) FROM notify_logs WHERE trade_no=?", (trade_no,))
orow = db_exec("SELECT notify_status, next_notify_at FROM orders WHERE trade_no=?", (trade_no,))
check("商户通知已排队（无 worker 时落重试队列标记）",
      (nrows and nrows[0][0] >= 1) or (orow and orow[0][0] == 1 and orow[0][1]),
      f"logs={nrows} order={orow}")

print("== D. 随机金额商户：收款页 → cashier 302 直跳上游 ==")
db_exec("UPDATE merchants SET random_enable=1, random_min=5, random_max=10 WHERE pid=1001")
st8, body8, _ = req("POST", "/paypage/order", {"token": paypage_token(), "money": ""})
check("随机金额收款页建单", body8.get("code") == 0, str(body8)[:300])
r_trade_no = body8["data"]["trade_no"]
cashier_path = body8["data"]["cashier_url"].replace(BASE, "")
_, r_status, loc_or_page = get_raw(cashier_path, follow=False)
check("cashier 链接 302 直跳上游（不展示收银台）",
      r_status == 302 and "paypage/" in (loc_or_page or ""), f"{r_status} {loc_or_page[:120]}")
row = db_exec("SELECT realmoney FROM orders WHERE trade_no=?", (r_trade_no,))
check("实付金额在随机区间 [5,10]", row and 5 <= float(row[0][0]) <= 10, str(row))
# 已支付后访问 cashier → 成功页
db_exec("UPDATE orders SET status=1, paid_at=? WHERE trade_no=?",
       (time.strftime("%Y-%m-%d %H:%M:%S"), r_trade_no))
_, s_status, s_page = get_raw(cashier_path)
check("已支付访问 cashier 显示成功页", s_status == 200 and "支付成功" in s_page)

# 收款页 GET 全链路（对齐旧版 paypage/index.php）：首访倒计时页 → go=1 建单 302 收银台 → 302 上游
pp_path = "/p/" + paypage_token()
_, g1_status, g1_page = get_raw(pp_path)
check("随机金额收款页首访渲染倒计时页（无生成付款订单按钮）",
      g1_status == 200 and "go=1" in g1_page and "生成付款订单" not in g1_page, g1_page[:160])
_, g2_status, g2_loc = get_raw(pp_path + "?go=1", follow=False)
check("go=1 服务端建单并 302 到收银台链接",
      g2_status == 302 and "/c/" in (g2_loc or ""), f"{g2_status} {g2_loc[:120]}")
_, g3_status, g3_loc = get_raw(g2_loc, follow=False)
check("收银台链接 302 直跳真实上游",
      g3_status == 302 and "paypage/" in (g3_loc or ""), f"{g3_status} {g3_loc[:120]}")

print("== E. API支付开关 ==")
ah = admin_login()
set_setting(ah, "api_pay_enable", False)
st9, body9, _ = create_order(f"LIVE-OFF{int(time.time())}", "10.00")
check("关闭后下单拒绝", body9.get("code") != 0 and "API支付接口未开放" in body9.get("message", ""), str(body9)[:200])
mh = merchant_login()
st10, body10, _ = req("GET", "/merchant-api/menuList", headers=mh)
names = [i["name"] for s in body10["data"] for i in s["items"]]
check("商户端菜单隐藏「API信息」", "API信息" not in names and "修改密码" in names, str(names))
st11, body11, _ = req("GET", "/merchant-api/account/apikey", headers=mh)
check("API信息页面接口仍可用（页面逻辑保留）",
      body11.get("code") == 0 and body11["data"].get("public_key")
      and body11["data"].get("platform_public_key"))
st12, body12, _ = req("GET", "/merchant-api/site-info")
check("site-info 下发开关 False", body12["data"]["features"]["api_pay_enable"] is False)
st13, body13, _ = req("POST", "/paypage/order", {"token": paypage_token(), "money": "6.00"})
check("关闭后收款页仍可收款", body13.get("code") == 0, str(body13)[:200])
set_setting(ah, "api_pay_enable", True)
st14, body14, _ = create_order(f"LIVE-ON{int(time.time())}", "10.00")
check("重新开启后下单恢复", body14.get("code") == 0, str(body14)[:200])
db_exec("UPDATE merchants SET random_enable=0 WHERE pid=1001")

print("== F. 退款 API：部分退款 + 剩余全额退款 ==")
tn_rf = f"LIVERF{int(time.time())}"
st15, body15, _ = create_order(tn_rf, "10.00")
trade_rf = body15["data"]["trade_no"]
m2 = re.search(r"/paypage/([\w]+)\?(.*)", body15["data"]["payurl"])
up2, rf_qs = m2.group(1), dict(_up.parse_qsl(m2.group(2)))
with urllib.request.urlopen(
        f"{MOCK}/mock-pay?" + _up.urlencode({
            "out": up2, "money": rf_qs.get("money", "10"),
            "type": rf_qs.get("type", "alipay"),
        })) as _r:
    pass
time.sleep(1.5)  # 等回调记账（10.00 → 商户到账 9.80）
bal0 = db_exec("SELECT balance FROM merchants WHERE pid=1001")[0][0]

rf1 = req("POST", f"/api/orders/{trade_rf}/refund", signed_params({
    "pid": PID, "refund_amount": "3.00", "out_refund_no": f"RF{int(time.time())}"}))
refund_no = rf1[1].get("data", {}).get("refund_no", "")
check("部分退款 3.00 成功", rf1[1].get("code") == 0 and rf1[1]["data"]["amount"] == "3.00"
      and rf1[1]["data"]["status"] == 1, str(rf1[1])[:200])

oq = req("GET", f"/api/orders/{trade_rf}", params=signed_params({"pid": PID}))
check("查单退款字段 PARTIAL",
      oq[1]["data"]["refund_status"] == "PARTIAL" and oq[1]["data"]["refunded_amount"] == "3",
      str(oq[1]["data"])[:200])

rfq = req("GET", f"/api/refunds/{refund_no}", params=signed_params({"pid": PID}))
check("退款单查询（refund_no）",
      rfq[1].get("code") == 0 and rfq[1]["data"]["amount"] == "3.00"
      and rfq[1]["data"]["status"] == 1, str(rfq[1])[:200])

rf2 = req("POST", f"/api/orders/{trade_rf}/refund", signed_params({"pid": PID}))
check("缺省金额=剩余全额 6.80 退款成功",
      rf2[1].get("code") == 0 and rf2[1]["data"]["amount"] == "6.80", str(rf2[1])[:200])

oq2 = req("GET", f"/api/orders/{trade_rf}", params=signed_params({"pid": PID}))
check("退满后查单 refund_status=FULL",
      oq2[1]["data"]["status"] == 1 and oq2[1]["data"]["refund_status"] == "FULL"
      and oq2[1]["data"]["refunded_amount"] == "9.8", str(oq2[1]["data"])[:200])

db_status = db_exec("SELECT status FROM orders WHERE trade_no=?", (trade_rf,))
check("退满订单库内状态=2（已退款）", db_status and db_status[0][0] == 2, str(db_status))

bal1 = db_exec("SELECT balance FROM merchants WHERE pid=1001")[0][0]
recs = db_exec("SELECT COUNT(*) FROM merchant_records WHERE biz_type='refund' AND order_trade_no=?",
               (trade_rf,))
check("余额扣减与退款流水核对（9.80 全退，2 条 refund 流水）",
      abs(float(bal1) - (float(bal0) - 9.8)) < 0.01 and recs and recs[0][0] == 2,
      f"bal0={bal0} bal1={bal1} recs={recs}")

print(f"\n===== 结果: {len(PASS)} 通过 / {len(FAIL)} 失败 =====")
if FAIL:
    print("失败项:", FAIL)
    sys.exit(1)
