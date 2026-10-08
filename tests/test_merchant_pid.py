"""随机商户号生成逻辑。"""

from app.core.security import hash_password
from app.models import Merchant
from app.services.merchant import PID_MIN, PID_RANGE, generate_merchant_pid


async def test_generate_merchant_pid_in_random_range(session):
    pid = await generate_merchant_pid(session)

    assert PID_MIN <= pid < PID_MIN + PID_RANGE


async def test_generate_merchant_pid_skips_existing(session, monkeypatch):
    """随机号已被占用时应重新生成。"""
    session.add(Merchant(
        pid=PID_MIN, username="occupied", password=hash_password("abc123"),
        status=1,
    ))
    await session.commit()

    values = iter([0, 77_654_321])

    def fake_randbelow(limit: int) -> int:
        assert limit == PID_RANGE
        return next(values)

    monkeypatch.setattr("app.services.merchant.secrets.randbelow", fake_randbelow)

    pid = await generate_merchant_pid(session)

    assert pid == 87_654_321
