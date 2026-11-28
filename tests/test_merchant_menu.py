"""商户端菜单端点：分组结构 + 按运行期开关显隐。"""

import pytest
from sqlmodel import select

from app.models import Merchant
from app.services.settings import set_settings


async def _headers(session) -> dict:
    m = (await session.exec(select(Merchant).where(Merchant.pid == 1001))).one()
    from app.core.security import issue_token
    token = await issue_token(guard="merchant", user_id=m.id, session=session)
    return {"Authorization": f"Bearer {token.access_token}"}


async def _menu(client, session) -> list[dict]:
    resp = await client.get("/merchant-api/menuList", headers=await _headers(session))
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    return body["data"]


@pytest.mark.asyncio
async def test_menu_default_sections(client, session):
    sections = await _menu(client, session)
    assert [s["label"] for s in sections] == [None, "个人资料", "查询", "下级代理", "其他"]

    def names(label):
        return [i["name"] for i in next(s for s in sections if s["label"] == label)["items"]]

    assert names(None) == ["用户中心"]
    assert names("个人资料") == ["API信息"]
    assert names("查询") == ["订单记录", "测试支付"]
    assert names("下级代理") == ["代理管理", "代理业绩"]
    # 外链默认空串 → 不下发
    assert names("其他") == ["开发文档"]

    # 修改资料/修改密码/结算记录/资金明细静态隐藏：页面与接口保留，菜单不下发
    all_names = [i["name"] for s in sections for i in s["items"]]
    assert not {"修改资料", "修改密码", "结算记录", "资金明细"} & set(all_names)


@pytest.mark.asyncio
async def test_menu_flag_filtering(client, session):
    await set_settings(session, {
        "agent_enable": False,  # 整组剔除
        "site_qq_group": "https://qm.qq.com/xxx",
    })
    sections = await _menu(client, session)
    labels = [s["label"] for s in sections]
    assert "下级代理" not in labels

    qq = next(i for i in next(s for s in sections if s["label"] == "其他")["items"]
              if i["name"] == "产品QQ群")
    assert qq["external"] is True
    assert qq["path"] == "https://qm.qq.com/xxx"
