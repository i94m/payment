"""FREE-33 并发实测脚本：对运行中的部署（uvicorn 直跑或 docker compose）压测。

场景：
1. site-info       纯读（无认证、无库）——网关自身吞吐底线
2. cashier-status  订单状态轮询（DB 读）——收银台轮询形态
3. create-order    签名下单全链路（DB 写 + 上游 mapi 提交）——核心支付路径

流量形态：双实例轮询（模拟主机 nginx RR）；X-Forwarded-For 轮换 200 个伪
客户端 IP（对齐生产「多付款人 IP」现实，也避免单 IP 触发 60/min 接口限流
——限流是策略不是容量，压测要测的是后者）。

用法：uv run python scripts/load_check.py [秒数，默认 20]
"""
import asyncio
import itertools
import json
import sys
import time
import statistics
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from _demo_key import DEMO_MERCHANT_PRIVATE_KEY
from app.lib.sign import make_rsa_sign

CANDIDATE_PORTS = [8000, 8001, 8002]  # compose 端口范围，实例实际分到的口运行期探测
PID = "1001"
DEMO_CHANNEL_CODE = "demo"  # seed_demo.py 建的演示编码
IPS = [f"10.66.{i // 250}.{i % 250 + 1}" for i in range(2000)]
_counter = itertools.count()


def signed_params(params: dict) -> dict:
    p = dict(params)
    p["timestamp"] = str(int(time.time()))
    p["sign"] = make_rsa_sign(p, DEMO_MERCHANT_PRIVATE_KEY)
    p["sign_type"] = "RSA2"
    return p


def pct(lat: list[float], p: float) -> float:
    if not lat:
        return 0.0
    s = sorted(lat)
    return s[min(len(s) - 1, int(len(s) * p))]


async def create_order_params(idx: int) -> dict:
    params = {
        "pid": PID, "channel_code": DEMO_CHANNEL_CODE,
        "type": "alipay", "out_trade_no": f"LOAD{int(time.time())}{idx}",
        "notify_url": "https://merchant.example/notify",
        "return_url": "http://127.0.0.1:8000/",
        "name": "压测订单", "money": "10.00",
    }
    return signed_params(params)


async def preload_orders(client: httpx.AsyncClient, n: int) -> list[str]:
    """预建 n 笔待支付单供轮询场景使用。"""
    trade_nos: list[str] = []
    for i in range(0, n, 20):
        chunk = await asyncio.gather(*(
            (lambda idx=i + j: asyncio.create_task(_post_order(client, idx)))()
            for j in range(min(20, n - i))))
        trade_nos += [t for t in chunk if t]
    return trade_nos


async def _post_order(client: httpx.AsyncClient, idx: int) -> str | None:
    r = await client.post(f"http://127.0.0.1:{port_for(idx)}/api/orders",
                          json=await create_order_params(idx),
                          headers={"X-Forwarded-For": IPS[idx % len(IPS)]})
    try:
        return r.json()["data"]["trade_no"]
    except Exception:
        return None


async def run_scenario(name: str, make_req, concurrency: int, duration: float,
                       client: httpx.AsyncClient):
    lat: list[float] = []
    errors: list[str] = []
    ok = 0
    stop_at = time.monotonic() + duration

    async def worker(wid: int):
        nonlocal ok
        while time.monotonic() < stop_at:
            t0 = time.monotonic()
            try:
                good = await make_req(client, wid)
                lat.append(time.monotonic() - t0)
                if good:
                    ok += 1
                else:
                    errors.append("biz-error")
            except Exception as e:
                lat.append(time.monotonic() - t0)
                errors.append(type(e).__name__)

    tasks = [asyncio.create_task(worker(w)) for w in range(concurrency)]
    t_start = time.monotonic()
    await asyncio.gather(*tasks)
    wall = time.monotonic() - t_start

    print(f"\n[{name}] 并发={concurrency} 时长={wall:.1f}s")
    print(f"  总请求 {len(lat)}  吞吐 {len(lat)/wall:.1f} rps  业务成功 {ok}"
          f"（{ok/len(lat)*100:.1f}%）" if lat else "  无请求")
    if errors:
        from collections import Counter
        print(f"  失败 {len(errors)}：{dict(Counter(errors).most_common(5))}")
    print(f"  延迟 p50={pct(lat,0.5)*1000:.0f}ms p90={pct(lat,0.9)*1000:.0f}ms "
          f"p99={pct(lat,0.99)*1000:.0f}ms max={max(lat)*1000:.0f}ms")
    return {"name": name, "total": len(lat), "rps": len(lat)/wall, "ok": ok,
            "errors": len(errors)}


def port_for(idx: int) -> int:
    return PORTS[idx % len(PORTS)]


async def main():
    global PORTS
    duration = float(sys.argv[1]) if len(sys.argv) > 1 else 20
    limits = httpx.Limits(max_connections=128, max_keepalive_connections=128)
    results = []
    async with httpx.AsyncClient(limits=limits, timeout=15) as client:
        # 探测端口范围内实际存活的实例（compose 范围分口顺序不保证 8000/8001）
        PORTS = []
        for p in CANDIDATE_PORTS:
            try:
                r = await client.get(f"http://127.0.0.1:{p}/health", timeout=2)
                if r.status_code == 200:
                    PORTS.append(p)
            except Exception:
                pass
        assert PORTS, "端口范围内无存活实例（8000-8002）"
        print(f"压测目标实例端口: {PORTS}")

        # ---- 场景 1：site-info 纯读 ----
        async def s1(c, wid):
            r = await c.get("http://127.0.0.1:%d/merchant-api/site-info" % port_for(wid))
            return r.status_code == 200 and r.json()["code"] == 0
        results.append(await run_scenario("site-info(纯读)", s1, 50, duration, client))

        # ---- 场景 2：cashier/status 轮询（DB 读）----
        trades = await preload_orders(client, 300)
        print(f"\n预建轮询订单 {len(trades)} 笔")
        trade_it = itertools.cycle(trades)

        async def s2(c, wid):
            tn = next(trade_it)
            r = await c.get(f"http://127.0.0.1:{port_for(wid)}/cashier/status",
                            params={"trade_no": tn},
                            headers={"X-Forwarded-For": next_ip()})
            return r.status_code == 200 and r.json()["code"] == 0
        results.append(await run_scenario("cashier/status(订单轮询)", s2, 50, duration, client))

        # ---- 场景 3：下单全链路（DB 写 + 上游提交）----
        idx_it = itertools.count(10_000_000)

        async def s3(c, wid):
            i = next(idx_it)
            r = await c.post(f"http://127.0.0.1:{port_for(wid)}/api/orders",
                             json=await create_order_params(i),
                             headers={"X-Forwarded-For": IPS[i % len(IPS)]})
            body = r.json()
            return r.status_code == 200 and body.get("code") == 0
        results.append(await run_scenario("create-order(下单全链路)", s3, 30, duration, client))

    print("\n===== 汇总 =====")
    for r in results:
        print(f"  {r['name']}: {r['rps']:.1f} rps, 成功率 {r['ok']}/{r['total']}, 失败 {r['errors']}")


_ip_iter = itertools.cycle(IPS)


def next_ip() -> str:
    return next(_ip_iter)


if __name__ == "__main__":
    asyncio.run(main())
