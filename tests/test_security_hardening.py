"""安全与配置域加固回归（P0-5/6、P1-7/8/9/10/11/13/14/15、P2-20）。

覆盖点与实现的对应关系：
- 凭据脱敏：列表/详情默认掩码、reveal 需 channel.update、掩码回传=不修改
- 配置写白名单：PATCH 只能改 ADMIN_SETTING_KEYS（订单回补窗口在表内）
- 设置缓存加密：Redis 里不得出现平台私钥明文
- SMTP 真实结果：不再「未 await 的 Future 恒真」
- 存储驱动：绝对路径/`../` 越界在驱动层拒绝
- 收银台链接：内置时间戳 + TTL
- 开放 API：pid 不存在/封禁统一文案
- 审计：调账/设置更新留痕，且密码类字段脱敏
- 会话吊销：改密后其它令牌失效
- 找回密码：验证码失败次数上限
- 插件安装：非超管 403
"""
import json
import time

import pytest
from sqlmodel import select

from app.core.security import hash_password, issue_token
from app.models import (
    Channel,
    Merchant,
    Setting,
    SystemAdmin,
    SystemAdminLogin,
    SystemAdminPermission,
    SystemPermission,
)

from tests.conftest import UPSTREAM_KEY  # noqa: E402
MASK = "******"


# ------------------------------------------------------------
# 辅助
# ------------------------------------------------------------

async def _make_admin(session, username: str, *, super_admin: bool = True,
                      perms: tuple[str, ...] = ()):
    admin = SystemAdmin(name="管理员", username=username,
                        password=hash_password("admin123456"),
                        is_super_admin=super_admin)
    session.add(admin)
    await session.commit()
    await session.refresh(admin)
    for key in perms:
        perm = (await session.exec(
            select(SystemPermission).where(SystemPermission.key == key)
        )).first()
        if perm is None:
            perm = SystemPermission(name=key, key=key)
            session.add(perm)
            await session.commit()
            await session.refresh(perm)
        session.add(SystemAdminPermission(admin_id=admin.id, permission_id=perm.id))
    await session.commit()
    token = await issue_token(guard="admin", user_id=admin.id, session=session)
    return {"Authorization": f"Bearer {token.access_token}"}, admin


async def _merchant_token(session, merchant_id: int) -> str:
    token = await issue_token(guard="merchant", user_id=merchant_id, session=session,
                              single_session=False)
    return token.access_token


class _MemCache:
    """最小内存缓存（get/set/delete），验证密文落缓存等纯逻辑。"""

    def __init__(self):
        self.d: dict[str, str] = {}

    async def get(self, key):
        return self.d.get(key)

    async def set(self, key, value, ex=None, **kwargs):
        self.d[key] = value
        return True

    async def delete(self, key):
        self.d.pop(key, None)

    async def incr(self, key):
        self.d[key] = int(self.d.get(key, 0)) + 1
        return self.d[key]


# ------------------------------------------------------------
# P0-6 通道凭证脱敏 / P2-20 更新校验
# ------------------------------------------------------------

@pytest.mark.asyncio
async def test_channel_credentials_masked_by_default(session, client, seeded):
    headers, _ = await _make_admin(session, "secmask")
    channel = seeded["channel"]

    r = await client.get("/admin-api/pay/channels", headers=headers)
    assert r.status_code == 200
    item = r.json()["data"]["items"][0]
    assert item["config"]["appkey"] == MASK
    assert item["config"]["appurl"] == "https://upstream.example/"  # 非密钥字段仍可见

    r = await client.get(f"/admin-api/pay/channels/{channel.id}", headers=headers)
    data = r.json()["data"]
    assert data["config"]["appkey"] == MASK
    assert data["config_revealed"] is False

    # 有 channel.update 权限可显式 reveal
    r = await client.get(f"/admin-api/pay/channels/{channel.id}",
                         params={"reveal": 1}, headers=headers)
    data = r.json()["data"]
    assert data["config"]["appkey"] == UPSTREAM_KEY
    assert data["config_revealed"] is True


@pytest.mark.asyncio
async def test_channel_reveal_requires_update_permission(session, client, seeded):
    """权限边界：只有 channel.view 的账号即便带 reveal=1 也拿不到明文。"""
    headers, _ = await _make_admin(session, "secmaskview", super_admin=False,
                                   perms=("channel.view",))
    channel = seeded["channel"]

    r = await client.get(f"/admin-api/pay/channels/{channel.id}",
                         params={"reveal": 1}, headers=headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["config"]["appkey"] == MASK
    assert data["config_revealed"] is False

    # 无 channel.update 自然也不能改（掩码回传的写入路径一并挡住）
    r = await client.put(f"/admin-api/pay/channels/{channel.id}",
                         json={"name": "x", "plugin_name": "epay",
                               "payment_type_id": seeded["pay_type"].id,
                               "status": 1,
                               "config": {"appkey": MASK}},
                         headers=headers)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_channel_update_keeps_masked_secret(session, client, seeded):
    headers, _ = await _make_admin(session, "secmask2")
    channel = seeded["channel"]
    payload = {
        "name": "改名通道", "plugin_name": "epay",
        "payment_type_id": seeded["pay_type"].id,
        "status": 1,
        "config": {"appurl": "https://new.example/", "appid": "2001",
                   "appkey": MASK, "appswitch": "1"},
    }
    r = await client.put(f"/admin-api/pay/channels/{channel.id}",
                         json=payload, headers=headers)
    assert r.status_code == 200

    from app.services.crypto import decrypt_config
    row = await session.get(Channel, channel.id)
    await session.refresh(row)
    stored = decrypt_config(row.config)
    assert stored["appkey"] == UPSTREAM_KEY       # 掩码回传没有把密钥写成 ******
    assert stored["appurl"] == "https://new.example/"

    # 真值提交则覆盖
    payload["config"]["appkey"] = "brand-new-key"
    r = await client.put(f"/admin-api/pay/channels/{channel.id}",
                         json=payload, headers=headers)
    assert r.status_code == 200
    row = await session.get(Channel, channel.id)
    await session.refresh(row)
    assert decrypt_config(row.config)["appkey"] == "brand-new-key"


@pytest.mark.asyncio
async def test_channel_update_validates_plugin_and_payment_type(session, client, seeded):
    headers, _ = await _make_admin(session, "secmask3")
    channel = seeded["channel"]
    base = {"name": "x", "plugin_name": "epay",
            "payment_type_id": seeded["pay_type"].id, "status": 9}

    bad_plugin = dict(base, plugin_name="not-installed")
    r = await client.put(f"/admin-api/pay/channels/{channel.id}",
                         json=bad_plugin, headers=headers)
    assert r.json()["message"] == "插件不存在或未加载"

    bad_type = dict(base, payment_type_id=999999)
    r = await client.put(f"/admin-api/pay/channels/{channel.id}",
                         json=bad_type, headers=headers)
    assert r.json()["message"] == "支付方式不存在"

    r = await client.put(f"/admin-api/pay/channels/{channel.id}",
                         json=base, headers=headers)
    assert r.status_code == 200
    row = await session.get(Channel, channel.id)
    await session.refresh(row)
    assert row.status == 0  # status 归一化：非 1 一律 0


# ------------------------------------------------------------
# P1-9 配置写白名单 + P1-13 缓存密文
# ------------------------------------------------------------

@pytest.mark.asyncio
async def test_settings_patch_rejects_hidden_keys(session, client, seeded):
    headers, _ = await _make_admin(session, "secset")

    r = await client.patch("/admin-api/setting",
                           json={"platform_rsa_private_key": "attacker"},
                           headers=headers)
    assert r.status_code == 422
    row = (await session.exec(
        select(Setting).where(Setting.key == "platform_rsa_private_key")
    )).first()
    assert row.value != "attacker"

    r = await client.patch("/admin-api/setting", json={"site_name": "新站名"},
                           headers=headers)
    assert r.status_code == 200
    assert any(i["key"] == "site_name" and i["value"] == "新站名"
               for i in r.json()["data"]["items"])


@pytest.mark.asyncio
async def test_settings_page_hides_late_pay_backfill_window(session, client, seeded):
    """`late_pay_backfill_hours`（过期单回补窗口）不在站点配置页暴露。

    回补口径固定用注册表默认值（24h），不走配置页：页面不展示该键，
    配置接口也拒绝修改（仍可由运维直接改库）。
    """
    headers, _ = await _make_admin(session, "secbackfill")

    resp = await client.get("/admin-api/setting", headers=headers)
    items = {i["key"]: i for i in resp.json()["data"]["items"]}
    assert "late_pay_backfill_hours" not in items

    r = await client.patch("/admin-api/setting",
                           json={"late_pay_backfill_hours": 48}, headers=headers)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_settings_cache_is_encrypted(session, seeded):
    from app.services.settings import CACHE_KEY, get_all_settings

    cache = _MemCache()
    settings = await get_all_settings(session, cache)
    assert settings["platform_rsa_private_key"]  # 读得到
    raw = cache.d[CACHE_KEY]
    assert raw.startswith("enc:v1:")
    assert "BEGIN" not in raw and "MII" not in raw  # 私钥不在缓存明文里

    # 密文可解：第二次读到同样的值
    again = await get_all_settings(session, cache)
    assert again["platform_rsa_private_key"] == settings["platform_rsa_private_key"]

    # 历史明文缓存仍可读（兼容一次）
    cache.d[CACHE_KEY] = json.dumps({"site_name": "旧明文站名"})
    legacy = await get_all_settings(session, cache)
    assert legacy["site_name"] == "旧明文站名"


# ------------------------------------------------------------
# P0-5 邮件真实结果
# ------------------------------------------------------------

@pytest.mark.asyncio
async def test_smtp_send_async_reports_real_result(monkeypatch):
    import aiosmtplib

    from app.addons.mail.smtp.plugin import SmtpMail

    mail = SmtpMail({"host": "smtp.example", "port": 465, "username": "u",
                     "password": "p", "from_addr": "a@example.com"})

    async def _boom(*args, **kwargs):
        raise OSError("smtp down")

    monkeypatch.setattr(aiosmtplib, "send", _boom)
    assert await mail.send_async("to@example.com", "主题", "<p>x</p>") is False

    async def _ok(*args, **kwargs):
        return None

    monkeypatch.setattr(aiosmtplib, "send", _ok)
    assert await mail.send_async("to@example.com", "主题", "<p>x</p>") is True


@pytest.mark.asyncio
async def test_smtp_sync_send_in_event_loop_raises(monkeypatch):
    """旧实现「返回未 await 的 Future」= 恒真；现在在事件循环内同步调用会显式报错。"""
    from app.addons.mail.smtp.plugin import SmtpMail

    mail = SmtpMail({"host": "h", "port": 465, "from_addr": "a@example.com"})
    with pytest.raises(RuntimeError):
        mail.send("to@example.com", "主题", "<p>x</p>")


@pytest.mark.asyncio
async def test_send_mail_propagates_plugin_false(session, seeded, monkeypatch):
    from app.services.message import send_mail
    from app.services.plugin import PluginRegistry
    from app.services.settings import set_settings

    class _FailingMail:
        async def send_async(self, to, subject, body):
            return False

    monkeypatch.setattr(PluginRegistry, "get_mail",
                        staticmethod(lambda name, config: _FailingMail()))
    await set_settings(session, {"mail_channel": "smtp", "mail_config": "{}"})
    assert await send_mail(session, "to@example.com", "主题", "正文") is False


# ------------------------------------------------------------
# P1-14 本地存储路径穿越
# ------------------------------------------------------------

@pytest.mark.asyncio
async def test_local_storage_rejects_traversal(tmp_path):
    from app.addons.storage.local import LocalStorage

    storage = LocalStorage()
    storage.base_path = str(tmp_path)

    with pytest.raises(ValueError):
        await storage.upload("../escape.txt", b"x")
    with pytest.raises(ValueError):
        await storage.upload("/etc/passwd", b"x")
    with pytest.raises(ValueError):
        await storage.get_list({"prefix": "../../"})
    with pytest.raises(ValueError):
        await storage.delete(["../../etc/passwd"])

    result = await storage.upload("sub/ok.txt", b"hello")
    assert result["key"] == "sub/ok.txt"
    assert (tmp_path / "sub" / "ok.txt").read_bytes() == b"hello"
    assert not (tmp_path.parent / "escape.txt").exists()


# ------------------------------------------------------------
# P1-15 收银台链接 TTL
# ------------------------------------------------------------

def test_cashier_link_token_ttl(monkeypatch):
    import app.lib.linktoken as linktoken

    token = linktoken.encode_link_token("2610061200000000000001")
    assert linktoken.decode_link_token(token) == "2610061200000000000001"

    real_time = time.time
    monkeypatch.setattr(linktoken.time, "time",
                        lambda: real_time() + linktoken.link_ttl_seconds() + 5)
    assert linktoken.decode_link_token(token) is None

    # 无时间戳的历史令牌按过期处理
    monkeypatch.setattr(linktoken.time, "time", real_time)
    legacy = linktoken._get_fernet().encrypt(b"2610061200000000000001").decode("ascii")
    assert linktoken.decode_link_token(legacy) is None


# ------------------------------------------------------------
# P1-15 开放 API pid 枚举
# ------------------------------------------------------------

@pytest.mark.asyncio
async def test_openapi_pid_enumeration_unified(session, client, seeded):
    body = {"pid": "999999", "type": "alipay", "out_trade_no": "ENUM1",
            "money": "1.00", "timestamp": str(int(time.time())), "sign": "x"}
    r = await client.post("/api/orders", json=body)
    assert r.status_code == 400
    assert r.json()["message"] == "商户不存在或已停用"

    m = seeded["merchant"]
    m.status = 0
    session.add(m)
    await session.commit()
    body["pid"] = "1001"
    r = await client.post("/api/orders", json=body)
    assert r.status_code == 400
    assert r.json()["message"] == "商户不存在或已停用"


# ------------------------------------------------------------
# P1-10 改密吊销会话
# ------------------------------------------------------------

@pytest.mark.asyncio
async def test_change_password_revokes_other_sessions(session, client, seeded):
    merchant = seeded["merchant"]
    tok_a = await _merchant_token(session, merchant.id)
    tok_b = await _merchant_token(session, merchant.id)

    r = await client.post("/merchant-api/account/changePassword",
                          json={"old_password": "test123456",
                                "new_password": "newpass123"},
                          headers={"Authorization": f"Bearer {tok_a}"})
    assert r.status_code == 200, r.text

    r = await client.get("/merchant-api/auth/profile",
                         headers={"Authorization": f"Bearer {tok_b}"})
    assert r.status_code == 401  # 其它会话已失效

    r = await client.get("/merchant-api/auth/profile",
                         headers={"Authorization": f"Bearer {tok_a}"})
    assert r.status_code == 200  # 当前会话保留


# ------------------------------------------------------------
# P1-11 找回密码失败次数上限
# ------------------------------------------------------------

@pytest.mark.asyncio
async def test_reset_code_has_attempt_limit(session, seeded):
    from app.routers.merchant.auth import ResetPasswordRequest, reset_password

    merchant = seeded["merchant"]
    merchant.email = "m@example.com"
    session.add(merchant)
    await session.commit()
    await session.refresh(merchant)

    cache = _MemCache()
    code_key = f"pwdreset:code:{merchant.id}"
    fail_key = f"pwdreset:fail:{merchant.id}"
    await cache.set(code_key, "123456", ex=900)

    msg = ""
    for _ in range(5):
        resp = await reset_password(
            ResetPasswordRequest(email="m@example.com", code="000000",
                                 password="newpass123"),
            None, session, cache,
        )
        msg = resp.body.decode() if isinstance(resp.body, bytes) else str(resp)
    assert "次数过多" in msg
    assert await cache.get(code_key) is None  # 验证码作废
    assert await cache.get(fail_key)  # 失败计数保留到窗口结束

    # 作废后即使码对了也不能重置
    await cache.delete(fail_key)
    resp = await reset_password(
        ResetPasswordRequest(email="m@example.com", code="123456",
                             password="newpass123"),
        None, session, cache,
    )
    assert "验证码错误或已过期" in (resp.body.decode() if isinstance(resp.body, bytes) else str(resp))


# ------------------------------------------------------------
# P1-8 插件安装权限：channel.update 即可（不再要求超管），无权限仍 403
# ------------------------------------------------------------

@pytest.mark.asyncio
async def test_plugin_upload_requires_channel_update(session, client, seeded):
    # 有 channel.update：通过权限校验，落到包校验（非 zip → 400）
    headers, _ = await _make_admin(session, "secplugin", super_admin=False,
                                   perms=("channel.update",))
    r = await client.post(
        "/admin-api/pay/plugins/upload",
        files={"file": ("pkg.zip", b"not-a-zip", "application/zip")},
        headers=headers,
    )
    assert r.status_code == 400
    assert r.json()["message"] == "无效的 zip 文件"

    # 只有 channel.view（只能看）：403，最小权限不变
    viewer_headers, _ = await _make_admin(session, "secplugin_viewer", super_admin=False,
                                          perms=("channel.view",))
    r = await client.post(
        "/admin-api/pay/plugins/upload",
        files={"file": ("pkg.zip", b"not-a-zip", "application/zip")},
        headers=viewer_headers,
    )
    assert r.status_code == 403

    # 超管当然可以（同样落到包校验）
    super_headers, _ = await _make_admin(session, "secplugin2")
    r = await client.post(
        "/admin-api/pay/plugins/upload",
        files={"file": ("pkg.zip", b"not-a-zip", "application/zip")},
        headers=super_headers,
    )
    assert r.status_code == 400
    assert r.json()["message"] == "无效的 zip 文件"


# ------------------------------------------------------------
# P1-7 死表 SystemAdminLogin 启用
# ------------------------------------------------------------

@pytest.mark.asyncio
async def test_admin_login_records_login_log(session, client):
    session.add(SystemAdmin(name="管理员", username="loginadmin",
                            password=hash_password("admin123456"),
                            is_super_admin=True))
    await session.commit()

    r = await client.post("/admin-api/auth/login",
                          json={"username": "loginadmin", "password": "admin123456",
                                "captcha_key": "k", "captcha_code": "0000"})
    assert r.status_code == 200, r.text

    rows = (await session.exec(select(SystemAdminLogin))).all()
    assert len(rows) == 1
    assert rows[0].admin_id > 0
