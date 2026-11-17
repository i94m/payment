"""通道后台页面相关回归：

- 站点配置白名单包含通道测试支付开关 / 测试商户 PID（漏放会导致通道测试
  一直报「请先在站点配置中配置测试商户PID」）
- 通道列表「调试日志」开关走独立接口，只改 debug_log，不重写费率/限额
- 插件 inputs 返回前端渲染口径的 label（插件 meta 里叫 title），且易支付
  不再暴露 appswitch 字段
"""
import pytest
from sqlmodel import select

from app.core.security import hash_password, issue_token
from app.models import Channel, SystemAdmin

MASK = "******"


async def _admin_headers(session, username: str = "chanadmin"):
    admin = SystemAdmin(name="管理员", username=username,
                        password=hash_password("admin123456"), is_super_admin=True)
    session.add(admin)
    await session.commit()
    await session.refresh(admin)
    token = await issue_token(guard="admin", user_id=admin.id, session=session)
    return {"Authorization": f"Bearer {token.access_token}"}


@pytest.mark.asyncio
async def test_test_pay_pid_is_editable_in_site_settings(session, client, seeded):
    """测试商户 PID 必须在站点配置白名单内：能出现在列表项、且 PATCH 不被拒。"""
    headers = await _admin_headers(session)

    r = await client.get("/admin-api/setting", headers=headers)
    assert r.status_code == 200
    keys = {item["key"] for item in r.json()["data"]["items"]}
    assert {"test_pay_enable", "test_pay_merchant_pid"} <= keys

    r = await client.patch("/admin-api/setting",
                           json={"test_pay_merchant_pid": 1001}, headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == 0, body
    saved = {i["key"]: i["value"] for i in body["data"]["items"]}
    assert saved["test_pay_merchant_pid"] == 1001


@pytest.mark.asyncio
async def test_toggle_debug_log_only_touches_that_field(session, client, seeded):
    """列表开关只改 debug_log：费率/限额等未上表单的字段不能被默认值覆盖。"""
    headers = await _admin_headers(session, "chanadmin2")
    channel = seeded["channel"]

    r = await client.put(f"/admin-api/pay/channels/{channel.id}/debug-log",
                         json={"enabled": True}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["debug_log"] == 1

    await session.refresh(channel)
    assert channel.debug_log == 1
    # 原有配置保持原值
    assert str(channel.merchant_rate) == "2.00"
    assert str(channel.cost_rate) == "1.00"
    assert str(channel.min_amount) == "1.00"

    r = await client.put(f"/admin-api/pay/channels/{channel.id}/debug-log",
                         json={"enabled": False}, headers=headers)
    assert r.status_code == 200, r.text
    await session.refresh(channel)
    assert channel.debug_log == 0


@pytest.mark.asyncio
async def test_plugin_inputs_use_label_and_drop_appswitch(session, client, seeded):
    headers = await _admin_headers(session, "chanadmin3")

    r = await client.get("/admin-api/pay/plugins", headers=headers)
    assert r.status_code == 200
    epay = next(p for p in r.json()["data"] if p["name"] == "epay")
    names = [i["name"] for i in epay["inputs"]]
    assert names == ["appurl", "appid", "appkey"]
    assert all(i.get("label") for i in epay["inputs"])
    assert "appswitch" not in names
    assert epay["supported_types"] == ["alipay", "wxpay"]

    # 详情同样返回 label（通道表单按它渲染）
    channel = (await session.exec(select(Channel))).first()
    r = await client.get(f"/admin-api/pay/channels/{channel.id}",
                         params={"reveal": 1}, headers=headers)
    inputs = r.json()["data"]["inputs"]
    assert [i["label"] for i in inputs] == ["接口地址", "商户ID", "商户密钥"]
