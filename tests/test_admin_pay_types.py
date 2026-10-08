"""admin 支付方式 CRUD：字段与校验规则。"""
import pytest

from app.core.security import hash_password
from app.models import SystemAdmin


@pytest.mark.asyncio
async def test_admin_pay_type_crud(session, client):
    session.add(SystemAdmin(
        name="管理员", username="admin",
        password=hash_password("admin123"), is_super_admin=True,
    ))
    await session.commit()

    from sqlmodel import select
    from app.core.security import issue_token

    admin = (await session.exec(
        select(SystemAdmin).where(SystemAdmin.username == "admin")
    )).first()
    token_obj = await issue_token(guard="admin", user_id=admin.id, session=session)
    token = token_obj.access_token
    headers = {"Authorization": f"Bearer {token}"}

    # 列表：种子支付方式带 device 字段，按 id 升序
    resp = await client.get("/admin-api/pay/types", headers=headers)
    assert resp.status_code == 200
    items = resp.json()["data"]
    assert items[0]["code"] == "alipay"
    assert items[0]["device"] == 0

    # 新增：非法调用值
    resp = await client.post("/admin-api/pay/types", headers=headers,
                             json={"code": "alipay-xx", "name": "X", "device": 0})
    assert resp.json()["message"] == "调用值不符合规则"

    # 新增：必填缺失
    resp = await client.post("/admin-api/pay/types", headers=headers,
                             json={"code": "newpay", "name": "", "device": 0})
    assert resp.json()["message"] == "请确保各项不能为空！"

    # 新增：同一调用值+设备重复
    resp = await client.post("/admin-api/pay/types", headers=headers,
                             json={"code": "alipay", "name": "支付宝2", "device": 0})
    assert resp.json()["message"] == "同一个调用值+支持设备不能重复"

    # 新增成功：新调用值，默认开启；补足到 id>=4（id<4 为系统自带保护段）
    new_id = None
    for code, name in [("np1", "填充1"), ("np2", "填充2"), ("newpay", "新支付")]:
        resp = await client.post("/admin-api/pay/types", headers=headers,
                                 json={"code": code, "name": name, "device": 2})
        assert resp.status_code == 200, resp.json()
        new_id = resp.json()["data"]["id"]
    assert new_id >= 4

    resp = await client.get("/admin-api/pay/types", headers=headers)
    item = next(x for x in resp.json()["data"] if x["id"] == new_id)
    assert item["device"] == 2 and item["status"] == 1

    # 编辑：改名 + 改设备；状态保持不变
    resp = await client.put(f"/admin-api/pay/types/{new_id}", headers=headers,
                            json={"code": "newpay", "name": "新支付改名", "device": 1})
    assert resp.status_code == 200
    resp = await client.get("/admin-api/pay/types", headers=headers)
    item = next(x for x in resp.json()["data"] if x["id"] == new_id)
    assert item["name"] == "新支付改名" and item["device"] == 1 and item["status"] == 1

    # 状态开关：列表开关直接切换状态
    resp = await client.put(f"/admin-api/pay/types/{new_id}/status", headers=headers,
                            json={"status": 0})
    assert resp.status_code == 200
    resp = await client.get("/admin-api/pay/types", headers=headers)
    item = next(x for x in resp.json()["data"] if x["id"] == new_id)
    assert item["status"] == 0

    # 删除保护：id<4 系统自带不可删
    resp = await client.delete("/admin-api/pay/types/1", headers=headers)
    assert resp.json()["message"] == "系统自带支付方式暂不支持删除"

    # 删除成功
    resp = await client.delete(f"/admin-api/pay/types/{new_id}", headers=headers)
    assert resp.status_code == 200

    # 今日收款统计接口可用
    resp = await client.get("/admin-api/pay/types/stats", headers=headers)
    assert resp.status_code == 200
