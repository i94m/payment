"""管理员 SSO 登录商户后台的会话语义。"""

from urllib.parse import parse_qs, urlparse

from main import app

from app.core.cache import DisabledCache, get_cache
from app.core.security import issue_token
from app.models import SystemAdmin


class MemoryCache:
    """跨 HTTP 请求共享的最小测试缓存。"""

    def __init__(self):
        self.items = {}

    async def get(self, key: str):
        return self.items.get(key)

    async def set(self, key: str, value, **kwargs):
        self.items[key] = value

    async def delete(self, key: str):
        self.items.pop(key, None)


async def test_sso_does_not_revoke_existing_merchant_sessions(client, seeded, session):
    m = seeded["merchant"]
    old_token = await issue_token(
        guard="merchant", user_id=m.id, session=session, name="existing",
        single_session=True,
    )

    admin = SystemAdmin(
        username="sso-admin", name="SSO Admin", password="x", is_super_admin=True,
    )
    session.add(admin)
    await session.commit()
    await session.refresh(admin)
    admin_token = await issue_token(guard="admin", user_id=admin.id, session=session)

    # client fixture 默认每次请求使用独立 DisabledCache；SSO 凭证需要跨请求共享。
    cache = MemoryCache()

    async def _shared_cache():
        yield cache

    async def _disabled_cache():
        yield DisabledCache()

    app.dependency_overrides[get_cache] = _shared_cache
    try:
        resp = await client.post(
            f"/admin-api/merchant/{m.id}/sso",
            headers={"Authorization": f"Bearer {admin_token.access_token}"},
        )
        body = resp.json()
        assert body["code"] == 0, body

        # 落地地址是 /#/sso-login?token=...（商户中心挂在 / 下），参数在 hash fragment 中。
        fragment_query = urlparse(body["data"]["url"]).fragment.split("?", 1)[1]
        sso_token = parse_qs(fragment_query)["token"][0]
        sso_resp = await client.post("/merchant-api/auth/sso", json={"token": sso_token})
        sso_body = sso_resp.json()
        assert sso_body["code"] == 0, sso_body

        profile_resp = await client.get(
            "/merchant-api/auth/profile",
            headers={"Authorization": f"Bearer {old_token.access_token}"},
        )
        profile_body = profile_resp.json()
        assert profile_body["code"] == 0, profile_body
        assert profile_body["data"]["username"] == m.username
    finally:
        app.dependency_overrides[get_cache] = _disabled_cache
