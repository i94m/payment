"""站点公开信息端点：admin/merchant 双前缀、无需登录、features 白名单下发。"""

import pytest

from app.services.settings import set_settings


@pytest.mark.asyncio
async def test_site_info_public(client):
    # 未携带任何 token 也能读取（登录页在认证前展示站点名）
    for prefix in ("/admin-api", "/merchant-api"):
        resp = await client.get(f"{prefix}/site-info")
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        assert body["data"]["site_name"]  # 默认「聚合支付」


@pytest.mark.asyncio
async def test_site_info_reports_image_version(client, monkeypatch):
    """后台侧边栏站点名称下方展示的版本号：取自构建期元信息（镜像 ENV）。"""
    from app.core.config import config

    monkeypatch.setattr(config, "APP_VERSION", "9.9.9")
    monkeypatch.setattr(config, "APP_REVISION", "deadbeef")
    for prefix in ("/admin-api", "/merchant-api"):
        data = (await client.get(f"{prefix}/site-info")).json()["data"]
        assert data["version"] == "9.9.9"
        assert data["revision"] == "deadbeef"


@pytest.mark.asyncio
async def test_site_info_features_defaults(client):
    resp = await client.get("/merchant-api/site-info")
    features = resp.json()["data"]["features"]
    # 默认全开（菜单开关），外链为空串；注册开关默认关闭
    for key in ("agent_enable", "recharge_enable", "paypage_enable"):
        assert features[key] is True
    assert features["merchant_reg_open"] is False
    assert features["site_qq_group"] == ""
    assert features["site_app_url"] == ""


@pytest.mark.asyncio
async def test_site_info_features_reflect_updates(client, session):
    await set_settings(session, {"agent_enable": False, "merchant_reg_open": True, "site_qq_group": "https://qm.qq.com/xxx"})
    resp = await client.get("/admin-api/site-info")
    data = resp.json()["data"]
    assert data["features"]["agent_enable"] is False
    assert data["features"]["merchant_reg_open"] is True
    assert data["features"]["site_qq_group"] == "https://qm.qq.com/xxx"
    # 公开端点只下发白名单键，绝不泄露敏感配置
    assert "platform_rsa_private_key" not in data
    assert "mail_config" not in data["features"]
