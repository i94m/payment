"""商户端公共接口。"""
import uuid

from fastapi import APIRouter

from app.core.limiter import rate_limit

from app.core.deps import CacheDep, SessionDep
from app.core.security import MerchantUser
from app.lib.captcha import generate_image_captcha
from app.responses import suc

router = APIRouter()


@router.get("/captcha")
async def captcha(cache: CacheDep, _rl=rate_limit(times=20)):
    """图片验证码。返回 {content: data URL, key}。"""
    content, code = generate_image_captcha()
    key = uuid.uuid4().hex[:26].lower()
    await cache.set(f"captcha:{key}", code, ex=300)
    return suc({"content": content, "key": key})


@router.get("/userInfo")
async def user_info(auth: MerchantUser):
    m = auth.user
    return suc({
        "id": m.id,
        "pid": m.pid,
        "username": m.username,
        "email": m.email or "",
        "phone": m.phone or "",
        "balance": str(m.balance),
    })


# 商户端菜单全量分组定义。
# flag: 为假剔除该项；group_flag: 为假剔除整组；url_setting: 取设置值作外链，空串剔除。
_MENU_SECTIONS: list[dict] = [
    # 首组无 label（与前端 sidebar.ts、admin 侧边栏一致）
    {"items": [
        {"key": "merchant.dashboard", "name": "用户中心", "path": "/", "icon": "icon-dashboard"},
    ]},
    {"label": "个人资料", "items": [
        # 下游 API 支付接口关闭时隐藏入口（页面与逻辑保留，直访 URL 仍可用）
        {"key": "merchant.security", "name": "API信息", "path": "/security", "icon": "icon-safe",
         "flag": "api_pay_enable"},
        {"key": "merchant.profile", "name": "修改资料", "path": "/user-center", "icon": "icon-user"},
        {"key": "merchant.password", "name": "修改密码", "path": "/password", "icon": "icon-lock"},
    ]},
    {"label": "查询", "items": [
        {"key": "merchant.order", "name": "订单记录", "path": "/orders", "icon": "icon-list"},
        {"key": "merchant.testpay", "name": "测试支付", "path": "/testpay", "icon": "icon-flask"},
        {"key": "merchant.settle", "name": "结算记录", "path": "/settle", "icon": "icon-thumb-up"},
        {"key": "merchant.record", "name": "资金明细", "path": "/records", "icon": "icon-file"},
    ]},
    {"label": "下级代理", "group_flag": "agent_enable", "items": [
        {"key": "merchant.agent", "name": "代理管理", "path": "/agents", "icon": "icon-users"},
        {"key": "merchant.agent_performance", "name": "代理业绩", "path": "/agent-performance", "icon": "icon-chart-bar"},
    ]},
    {"label": "其他", "items": [
        {"key": "merchant.doc", "name": "开发文档", "path": "/docs/", "icon": "icon-book", "external": True},
        {"key": "merchant.qq_group", "name": "产品QQ群", "path": "", "icon": "icon-brand-qq",
         "url_setting": "site_qq_group", "external": True},
        {"key": "merchant.app_download", "name": "APP下载", "path": "", "icon": "icon-device-mobile",
         "url_setting": "site_app_url", "external": True},
    ]},
]


@router.get("/menuList")
async def menu_list(auth: MerchantUser, session: SessionDep, cache: CacheDep):
    """商户端菜单（单一角色）：分组结构 + 按运行期开关过滤，与前端 merchant/sidebar.ts 对齐。"""
    from app.services.settings import build_public_features, get_all_settings, setting_enabled
    settings = await get_all_settings(session, cache=cache)

    sections = []
    for section in _MENU_SECTIONS:
        if section.get("group_flag") and not setting_enabled(settings.get(section["group_flag"])):
            continue
        items = []
        for item in section["items"]:
            flag = item.get("flag")
            if flag and not setting_enabled(settings.get(flag)):
                continue
            url_setting = item.get("url_setting")
            if url_setting:
                url = str(settings.get(url_setting) or "").strip()
                if not url:
                    continue
                items.append({**item, "path": url})
            else:
                items.append(item)
        if items:
            sections.append({"label": section.get("label"), "items": items})
    return suc(sections)
