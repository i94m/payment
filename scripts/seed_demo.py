"""演示数据种子：演示商户 + epay 通道（指向本地模拟上游）。

运行：uv run python scripts/seed_demo.py
环境变量：DEMO_APPURL 覆盖通道上游地址（默认 http://127.0.0.1:9000/）
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import select

from app.core.database import session_maker
from app.core.security import hash_password
from app.models import Channel, ChannelCode, ChannelCodeItem, Merchant, PaymentType
from app.services.apikey import get_api_secret, set_api_secret
from app.services.crypto import encrypt_config

from _demo_key import DEMO_MERCHANT_PUBLIC_KEY

DEMO_APPKEY = "demo-app-key-123"  # 模拟上游通道密钥（上游协议 MD5，与商户 API 无关）
DEMO_APPURL = os.environ.get("DEMO_APPURL", "http://127.0.0.1:9000/")


async def main():
    async with session_maker() as session:
        # 演示商户：public_key 固定为演示公钥（配套私钥见 scripts/_demo_key.py，
        # 演示脚本用它签名开放 API 请求）；生产商户请走面板「一键生成密钥对」
        m = (await session.exec(
            select(Merchant).where(Merchant.pid == 1001)
        )).first()
        if not m:
            m = Merchant(
                pid=1001, username="demo", password=hash_password("demo123456"),
                status=1, rate=None, public_key=DEMO_MERCHANT_PUBLIC_KEY,
                # 演示脚本/文档示例用 RSA2：demo 商户开启 RSA2；MD5 密钥创建即生成
                rsa2_enable=True,
            )
            set_api_secret(m)
            session.add(m)
            await session.commit()
            await session.refresh(m)
            print("[商户] 已创建 pid=1001 demo/demo123456（演示密钥对见 scripts/_demo_key.py）")
        else:
            if not get_api_secret(m):
                set_api_secret(m)
                session.add(m)
                await session.commit()
                print("[商户] 已补配 MD5 签名密钥")
            if not m.public_key:
                m.public_key = DEMO_MERCHANT_PUBLIC_KEY
                session.add(m)
                await session.commit()
                print("[商户] 已补配演示公钥（原 public_key 为空）")
            if not m.rsa2_enable:
                # 演示脚本/e2e 用 RSA2 签名，demo 商户需开启（生产商户按需在面板开启）
                m.rsa2_enable = True
                session.add(m)
                await session.commit()
                print("[商户] 已开启演示商户 RSA2 签名")
            print(f"[商户] 已存在 pid={m.pid} username={m.username}")

        # 支付方式
        pt = (await session.exec(
            select(PaymentType).where(PaymentType.code == "alipay")
        )).first()
        if not pt:
            pt = PaymentType(code="alipay", name="支付宝", status=1, sort=1)
            session.add(pt)
            await session.commit()
            await session.refresh(pt)
            print("[支付方式] 已创建 alipay")
        else:
            print(f"[支付方式] 已存在 {pt.code}")

        # epay 通道 → 本地模拟上游
        ch = (await session.exec(
            select(Channel).where(Channel.plugin_name == "epay")
        )).first()
        if not ch:
            ch = Channel(
                name="本地模拟上游", plugin_name="epay", payment_type_id=pt.id,
                merchant_rate=2, cost_rate=1, status=1, mode=0,
                min_amount=1, max_amount=0, day_limit=0,
                config=encrypt_config({
                    "appurl": DEMO_APPURL,
                    "appid": "2001",
                    "appkey": DEMO_APPKEY,
                    "appswitch": "1",
                }),
            )
            session.add(ch)
            await session.commit()
            print(f"[通道] 已创建 id={ch.id} 模拟上游 {DEMO_APPURL}")
        else:
            print(f"[通道] 已存在 id={ch.id}")

        # 演示编码：新系统「编码必传」默认全开，demo 商户绑定一个演示编码，
        # demo.py / e2e 脚本下单时携带 channel_code=demo
        code = (await session.exec(
            select(ChannelCode).where(ChannelCode.code == "demo")
        )).first()
        if not code:
            code = ChannelCode(code="demo", name="演示编码", mode=0, status=1,
                               remark="本地演示/冒烟脚本使用")
            session.add(code)
            await session.commit()
            await session.refresh(code)
            session.add(ChannelCodeItem(code_id=code.id, channel_id=ch.id,
                                        weight=1, status=1, sort=0))
            await session.commit()
            print(f"[编码] 已创建 channel_code=demo（绑定通道 id={ch.id}）")
        else:
            print("[编码] 已存在 channel_code=demo")

    print("\n演示环境就绪。")


if __name__ == "__main__":
    asyncio.run(main())
