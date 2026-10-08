"""本地模拟上游网关（仅演示用）。

模拟上游网关的两个接口：
- POST /mapi.php          下单 → 返回 payurl（指向本地模拟收银页）
- GET  /paypage/{trade_no} 模拟付款页，点击"模拟支付成功"后，
  由本服务向平台 /callbacks/epay/{trade_no} 发送签名通知，完成闭环。

启动：uv run python scripts/mock_upstream.py   （端口 9000）
"""
import hashlib
import json
import os
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APPKEY = "demo-app-key-123"          # 与演示通道凭证中的 appkey 一致
PLATFORM = os.environ.get("PLATFORM", "http://127.0.0.1:8000")   # 平台地址（回调目标）
PORT = int(os.environ.get("PORT", "9000"))


def _sign(params: dict, key: str) -> str:
    items = sorted(
        (k, v) for k, v in params.items()
        if k not in ("sign", "sign_type") and v is not None and v != ""
        and not isinstance(v, (list, dict))
    )
    raw = "&".join(f"{k}={v}" for k, v in items) + key
    return hashlib.md5(raw.encode()).hexdigest()


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: str, ctype: str = "application/json", code: int = 200):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path.startswith("/mapi.php"):
            length = int(self.headers.get("Content-Length", 0))
            params = {k: v[0] for k, v in urllib.parse.parse_qs(
                self.rfile.read(length).decode()).items()}
            out = params.get("out_trade_no", "")
            qs = urllib.parse.urlencode({
                "money": params.get("money", ""),
                "type": params.get("type", "alipay"),
                "name": params.get("name", ""),
            })
            self._send(json.dumps({
                "code": 1,
                "payurl": f"http://127.0.0.1:{PORT}/paypage/{out}?{qs}",
            }))
        else:
            self._send(json.dumps({"code": -1, "msg": "unknown"}), code=404)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        if parsed.path.startswith("/api.php"):
            # 退款：返回 code=0 表示成功（与上游约定一致）
            if qs.get("act") == "refund":
                self._send(json.dumps({"code": 0, "msg": "退款成功"}))
            else:
                self._send(json.dumps({"code": -1, "msg": "unknown"}))
        elif parsed.path.startswith("/paypage/"):
            out = parsed.path.split("/")[-1]
            money = qs.get("money", "")
            ptype = qs.get("type", "alipay")
            name = qs.get("name", "商品")
            self._send(f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>模拟上游 - 付款页</title>
<style>body{{font-family:system-ui;display:flex;justify-content:center;align-items:center;height:100vh;background:#f5f6f7;margin:0}}
.box{{background:#fff;padding:40px 48px;border-radius:12px;box-shadow:0 2px 12px rgba(0,0,0,.08);text-align:center}}
.amount{{font-size:32px;font-weight:700;color:#1d2129;margin:12px 0 24px}}
button{{padding:12px 40px;border:none;border-radius:8px;background:#00b42a;color:#fff;font-size:16px;cursor:pointer}}</style></head>
<body><div class="box"><p>【模拟上游】订单 {out}</p><p>{name}</p>
<div class="amount">¥{money}</div>
<button onclick="location.href='/mock-pay?out={out}&money={money}&type={ptype}'">模拟支付成功</button>
</div></body></html>""", "text/html")
        elif parsed.path.startswith("/mock-pay"):
            out = qs.get("out", "")
            params = {
                "pid": "2001",
                "name": qs.get("name", "商品"),
                "money": qs.get("money", ""),
                "out_trade_no": out,
                "trade_no": "UP" + out[-8:],
                "type": qs.get("type", "alipay"),
                "trade_status": "TRADE_SUCCESS",
            }
            params["sign"] = _sign(params, APPKEY)
            params["sign_type"] = "MD5"
            url = f"{PLATFORM}/callbacks/epay/{out}?" + urllib.parse.urlencode(params)
            try:
                with urllib.request.urlopen(url, timeout=10) as r:
                    ack = r.read().decode()
            except Exception as e:
                ack = f"EXCEPTION: {e}"
            self._send(f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>模拟上游 - 支付完成</title></head>
<body style="font-family:system-ui;text-align:center;padding-top:80px">
<h2>模拟支付完成</h2><p>平台回调应答：<b>{ack}</b></p>
<p>支付成功（模拟）</p>

</body></html>""", "text/html")
        else:
            self._send(json.dumps({"code": -1, "msg": "unknown"}), code=404)

    def log_message(self, *args):
        pass


class _Server(ThreadingHTTPServer):
    """压测友好：默认 listen backlog 只有 5，并发突刺会直接丢弃 SYN
    （表现为平台侧「上游通道连接失败」）；放大 backlog 并开启 keepalive。"""
    request_queue_size = 256
    daemon_threads = True


if __name__ == "__main__":
    # HTTP/1.1 keepalive：平台侧共享 httpx 客户端会复用连接，省去逐请求 TCP/线程开销
    Handler.protocol_version = "HTTP/1.1"
    print(f"模拟上游网关已启动: http://127.0.0.1:{PORT}  (appkey={APPKEY})")
    _Server(("0.0.0.0", PORT), Handler).serve_forever()
