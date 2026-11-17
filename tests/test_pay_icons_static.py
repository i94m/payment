"""内置支付方式图标库：static/icons 挂载在 /icons，源码入库、随镜像下发。"""
import pytest

from app.commands.db import PAY_TYPE_SEEDS


@pytest.mark.asyncio
async def test_builtin_pay_icons_served(client):
    codes = {s["code"] for s in PAY_TYPE_SEEDS}
    assert codes == {"alipay", "wxpay", "qqpay", "bank", "jdpay", "paypal"}
    for code in codes:
        resp = await client.get(f"/icons/pay/{code}.svg")
        assert resp.status_code == 200, code
        assert "image/svg" in resp.headers["content-type"]
