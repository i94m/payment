"""db init 支付方式种子：补齐 6 项内置方式并回填默认图标，不删除自定义/存量记录。"""
import pytest
from sqlmodel import select

from app.commands.db import PAY_TYPE_SEEDS, _seed_pay_types
from app.models import PaymentType


@pytest.mark.asyncio
async def test_seed_pay_types_fills_builtins_with_icons(session):
    # 模拟旧库：只有 alipay（无图标），另有一个后台自定义支付方式
    session.add(PaymentType(code="alipay", name="支付宝", status=1, sort=1))
    session.add(PaymentType(code="custom", name="自定义", status=1, sort=9))
    await session.commit()

    await _seed_pay_types(session)

    rows = {p.code: p for p in (await session.exec(select(PaymentType))).all()}
    seeds = {s["code"]: s for s in PAY_TYPE_SEEDS}
    assert set(seeds) <= set(rows)          # 内置 6 项全部补齐
    assert "custom" in rows                 # 自定义支付方式不受影响
    # 存量 alipay 不重复插入，且空图标被回填
    assert rows["alipay"].icon == seeds["alipay"]["icon"]
    for code, seed in seeds.items():
        assert rows[code].icon == seed["icon"]

    # 幂等：再次执行不应产生重复记录
    await _seed_pay_types(session)
    again = (await session.exec(select(PaymentType))).all()
    assert len(again) == len(rows)
