"""商户端资金明细开关（merchant_records_enable）：站点配置页可改 + 菜单隐藏 + 接口级拦截。

开关语义：
- 开（默认）：商户端「资金明细」接口正常；菜单入口已静态下线（hidden），恢复需改 sidebar/common
- 关：接口 403（页面直访由前端按 404 处理）
- 结算记录（/record/settlements）不受这个开关影响
"""
import pytest
from sqlmodel import select

from app.models import Merchant
from app.services.settings import set_settings


async def _headers(session) -> dict:
    m = (await session.exec(select(Merchant).where(Merchant.pid == 1001))).one()
    from app.core.security import issue_token
    token = await issue_token(guard="merchant", user_id=m.id, session=session)
    return {"Authorization": f"Bearer {token.access_token}"}


async def _admin_headers(session) -> dict:
    from app.core.security import hash_password, issue_token
    from app.models import SystemAdmin

    admin = (await session.exec(select(SystemAdmin))).first()
    if admin is None:
        admin = SystemAdmin(name="管理员", username="admin",
                            password=hash_password("admin123"), is_super_admin=True)
        session.add(admin)
        await session.commit()
    token = await issue_token(guard="admin", user_id=admin.id, session=session)
    return {"Authorization": f"Bearer {token.access_token}"}


@pytest.mark.asyncio
async def test_switch_exposed_in_admin_settings_page(client, seeded, session):
    """站点配置页可读写，且开关随 /site-info 下发给商户端。"""
    adm = await _admin_headers(session)

    items = (await client.get("/admin-api/setting", headers=adm)).json()["data"]["items"]
    item = next(i for i in items if i["key"] == "merchant_records_enable")
    # 每项都要有独立说明（站点配置页逐项渲染，不再用组级说明）
    assert item["description"].strip()

    r = await client.patch("/admin-api/setting", json={"merchant_records_enable": False}, headers=adm)
    assert r.json()["code"] == 0, r.json()

    features = (await client.get("/merchant-api/site-info")).json()["data"]["features"]
    assert features["merchant_records_enable"] is False

    # 白名单之外的运维键仍不可改
    bad = await client.patch("/admin-api/setting", json={"late_pay_backfill_hours": 1}, headers=adm)
    assert bad.status_code == 422

    r2 = await client.patch("/admin-api/setting", json={"merchant_records_enable": True}, headers=adm)
    assert r2.json()["code"] == 0, r2.json()


@pytest.mark.asyncio
async def test_records_menu_statically_hidden(client, seeded, session):
    """资金明细入口已静态下线：开关开/关都不下发（开关仍决定接口是否 403）。"""
    h = await _headers(session)
    names = lambda sections: [i["name"] for s in sections for i in s["items"]]
    assert "资金明细" not in names((await client.get("/merchant-api/menuList", headers=h)).json()["data"])

    await set_settings(session, {"merchant_records_enable": False})
    assert "资金明细" not in names((await client.get("/merchant-api/menuList", headers=h)).json()["data"])


@pytest.mark.asyncio
async def test_switch_off_blocks_records_api_only(client, seeded, session):
    """关闭后资金明细接口 403，结算记录不受影响。"""
    h = await _headers(session)
    assert (await client.get("/merchant-api/record/list", headers=h)).json()["code"] == 0
    assert (await client.get("/merchant-api/record/settlements", headers=h)).json()["code"] == 0

    await set_settings(session, {"merchant_records_enable": False})

    resp = await client.get("/merchant-api/record/list", headers=h)
    assert resp.status_code == 403, resp.text
    assert "资金明细功能已关闭" in resp.json()["message"]

    # 结算记录是独立功能，不受该开关约束
    assert (await client.get("/merchant-api/record/settlements", headers=h)).json()["code"] == 0

    await set_settings(session, {"merchant_records_enable": True})
    assert (await client.get("/merchant-api/record/list", headers=h)).json()["code"] == 0
