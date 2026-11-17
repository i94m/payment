"""db init 支付方式种子：只播种 alipay/wxpay，旧内置空记录在初始化时清理。"""
import pytest
from sqlmodel import select

from app.commands.db import _seed_pay_types
from app.models import Channel, PaymentType


@pytest.mark.asyncio
async def test_seed_pay_types_prunes_legacy(session):
    # 模拟旧库：种子收敛前落下的 qqpay / bank，另有一个后台自定义支付方式
    session.add(PaymentType(code="qqpay", name="QQ钱包", status=1, sort=3))
    bank = PaymentType(code="bank", name="云闪付", status=1, sort=4)
    session.add(bank)
    session.add(PaymentType(code="custom", name="自定义", status=1, sort=9))
    await session.commit()

    # bank 仍被通道引用 → 保留，避免通道失效
    session.add(Channel(name="云闪付通道", plugin_name="epay",
                        payment_type_id=bank.id, config="{}"))
    await session.commit()

    await _seed_pay_types(session)

    rows = {p.code for p in (await session.exec(select(PaymentType))).all()}
    assert {"alipay", "wxpay"} <= rows   # 种子补齐
    assert "qqpay" not in rows           # 未被引用的旧内置 → 删除
    assert "bank" in rows                # 被通道引用 → 保留
    assert "custom" in rows              # 自定义支付方式不受影响
