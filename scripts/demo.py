"""演示：调用开放 API 创建一笔订单并打印可点击链接（RSA2 签名）。

运行（需先启动平台与模拟上游）：uv run python scripts/demo.py [金额]
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _demo_key import DEMO_MERCHANT_PRIVATE_KEY
from app.lib.sign import make_rsa_sign

PLATFORM = "http://127.0.0.1:8000"
PID = "1001"
OUT_TRADE_NO = "DEMO" + time.strftime("%H%M%S")


def signed_params(params: dict) -> dict:
    p = dict(params)
    p["timestamp"] = str(int(time.time()))
    p["sign"] = make_rsa_sign(p, DEMO_MERCHANT_PRIVATE_KEY)
    p["sign_type"] = "RSA2"
    return p


def main():
    money = sys.argv[1] if len(sys.argv) > 1 else "10.00"
    params = signed_params({
        "pid": PID,
        "type": "alipay",
        "out_trade_no": OUT_TRADE_NO,
        "notify_url": "https://merchant.example/notify",  # 演示用占位
        "return_url": PLATFORM + "/",
        "name": "演示商品-测试支付",
        "money": money,
    })

    req = urllib.request.Request(
        PLATFORM + "/orders",
        data=json.dumps(params).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        body = json.loads(r.read())

    print(json.dumps(body, ensure_ascii=False, indent=2))
    if body.get("code") == 0:
        d = body["data"]
        print("\n=== 点击下面链接体验完整支付流程 ===")
        print(f"收银台: {d['cashier_url']}")
        if d.get("payurl"):
            print(f"上游付款页: {d['payurl']}  （点“模拟支付成功”完成闭环）")


if __name__ == "__main__":
    main()
