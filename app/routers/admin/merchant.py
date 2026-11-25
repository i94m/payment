"""admin 商户管理：

- CRUD / 启停 / 删除 / 重置密钥 / 余额调整（必写流水）/ 流水查询
- 支付/结算独立开关（pay: 0=关 1=开 2=待审核；settle: 0/1）
- 随机金额（单个/批量，min==max 固定金额；可开启「仅整数金额」）与限制拉单（同 IP 每分钟建单上限）
- 扣量（商户级三字段：开关 / 频次 / 起扣金额；单个 + 批量开关，批量开必须带频次）
- 配置锁定（fixed）：免受批量随机金额影响
- 精细搜索（按字段）+ 状态筛选 + 排序 + 高级搜索（N 天无订单）+ Excel 导出
- SSO 一键登录商户后台（一次性 token，60 秒有效）
"""
import logging
import secrets
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import Depends, APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import BaseModel
from sqlalchemy import exists as sa_exists
from sqlmodel import col, func, select

from app.core.deps import CacheDep, SessionDep
from app.core.security import (
    AdminUser, AuthResult, PermissionChecker, hash_password, revoke_all_tokens,
)
from app.lib.export import export_xlsx
from app.models import Channel, Merchant, MerchantRecord, Order, PaymentType, Settlement
from app.responses import err, paginate, suc
from app.routers.common.params import parse_int
from app.services.merchant import generate_merchant_pid
from app.services.balance import change_merchant_balance, dmoney
from app.services.settings import get_all_settings
from app.services.test_pay import TEST_MERCHANT_PID
from app.lib.sign import derive_public_key, pem_to_flat
from app.services.apikey import get_api_secret, set_api_secret

router = APIRouter()

# 审计日志独立命名空间（作为 "app" 的子 logger，handler 挂在父级 "app" 上，
# 记录向上冒泡写进同一份 app.log；名字带 audit 便于 grep 出后台敏感操作）
logger = logging.getLogger("app.audit")


# 列表搜索字段白名单（column 参数）
_SEARCH_FIELDS = {
    "pid": col(Merchant.pid),                # 商户号
    "username": col(Merchant.username),      # 登录用户名
    "account": col(Merchant.settle_account), # 结算账号
    "settle_name": col(Merchant.settle_name),# 结算姓名
    "url": col(Merchant.url),                # 域名
    "qq": col(Merchant.qq),
    "phone": col(Merchant.phone),
    "email": col(Merchant.email),
}

# 列表排序（order 参数）
_SORTS = {
    "": col(Merchant.id).desc(),
    "money_desc": col(Merchant.balance).desc(),
    "money_asc": col(Merchant.balance).asc(),
    "lasttime_desc": col(Merchant.last_login_at).desc(),
    "lasttime_asc": col(Merchant.last_login_at).asc(),
}

# 列头排序（order_by 参数，前端 DataTable 传 `key` / `-key`）
_ORDER_BY_FIELDS = {
    "username": col(Merchant.username),
    "pid": col(Merchant.pid),
    "balance": col(Merchant.balance),
    "status": col(Merchant.status),
    "pay_status": col(Merchant.pay_status),
    "settle_status": col(Merchant.settle_status),
    "fixed": col(Merchant.fixed),
    "last_login_at": col(Merchant.last_login_at),
    "created_at": col(Merchant.created_at),
}


def _dump_merchant(m: Merchant) -> dict:
    secret = get_api_secret(m)
    return {
        "id": m.id,
        "pid": m.pid,
        "username": m.username,
        "public_key": pem_to_flat(m.public_key or ""),
        "api_secret_masked": (f"{secret[:4]}****{secret[-4:]}" if secret else ""),
        "has_api_secret": bool(secret),
        "rsa2_enable": bool(m.rsa2_enable),
        "balance": str(m.balance),
        "frozen_balance": str(m.frozen_balance),
        "status": m.status,
        "rate": str(m.rate) if m.rate is not None else "",
        "default_notify_url": m.default_notify_url or "",
        "default_return_url": m.default_return_url or "",
        "email": m.email or "",
        "phone": m.phone or "",
        "qq": m.qq or "",
        "url": m.url or "",
        "allowed_domains": m.allowed_domains or "",
        "pay_status": m.pay_status,
        "settle_status": m.settle_status,
        "random_enable": bool(m.random_enable),
        "random_min": str(m.random_min),
        "random_max": str(m.random_max),
        "random_integer": bool(m.random_integer),
        "random_ratelimit": m.random_ratelimit,
        "deduct_enable": bool(m.deduct_enable),
        "deduct_frequency": m.deduct_frequency,
        "deduct_minmoney": str(m.deduct_minmoney),
        "fixed": bool(m.fixed),
        "settle_type": m.settle_type,     # 1支付宝 2微信 3QQ 4银行卡
        "settle_account": m.settle_account or "",
        "settle_name": m.settle_name or "",
        "auto_settle": bool(m.auto_settle),
        "created_at": m.created_at.strftime("%Y-%m-%d %H:%M:%S") if m.created_at else "",
        "last_login_at": m.last_login_at.strftime("%Y-%m-%d %H:%M:%S") if m.last_login_at else "",
    }


def _build_query(body: dict):
    """列表/导出共用查询构造。body 为前端过滤参数。"""
    # 系统测试商户（测试单挂载点）不参与商户管理/统计
    query = select(Merchant).where(col(Merchant.pid) != TEST_MERCHANT_PID)
    # 关键字：未指定字段时按 商户号/用户名 统一模糊
    keyword = str(body.get("keyword", "") or "").strip()
    search_field = str(body.get("search_field", "") or "")
    if keyword:
        if search_field in _SEARCH_FIELDS:
            query = query.where(_SEARCH_FIELDS[search_field].contains(keyword))
        else:
            query = query.where(
                (col(Merchant.username).contains(keyword))
                | (col(Merchant.pid).contains(keyword))
            )
    # 状态筛选（dstatus 参数）
    dstatus = str(body.get("dstatus", "") or "")
    if dstatus == "status_1":
        query = query.where(Merchant.status == 1)
    elif dstatus == "status_0":
        query = query.where(Merchant.status == 0)
    elif dstatus == "pay_1":
        query = query.where(Merchant.pay_status == 1)
    elif dstatus == "pay_0":
        query = query.where(Merchant.pay_status == 0)
    elif dstatus == "pay_2":
        query = query.where(Merchant.pay_status == 2)
    elif dstatus == "settle_1":
        query = query.where(Merchant.settle_status == 1)
    elif dstatus == "settle_0":
        query = query.where(Merchant.settle_status == 0)
    # 高级搜索：超过 N 天无订单的用户（非法值 400，不再静默回落成 0）
    order_days = parse_int(body.get("order_days"), "order_days", default=0, minimum=0) or 0
    if order_days > 0:
        cutoff = datetime.now() - timedelta(days=order_days)
        recent_order = select(Order.id).where(
            col(Order.merchant_id) == col(Merchant.id),
            col(Order.created_at) >= cutoff,
        ).exists()
        query = query.where(~recent_order)
    # 排序：列头排序（order_by）优先，其余沿用 order 兼容参数
    order_by = str(body.get("order_by", "") or "").strip()
    sort_col = _ORDER_BY_FIELDS.get(order_by.lstrip("-")) if order_by else None
    if sort_col is not None:
        primary = sort_col.desc() if order_by.startswith("-") else sort_col.asc()
        # 追加主键倒序，保证翻页时同值行顺序稳定
        query = query.order_by(primary, col(Merchant.id).desc())
    else:
        order = str(body.get("order", "") or "")
        query = query.order_by(_SORTS.get(order, _SORTS[""]))
    return query


async def _get_merchant_or_404(session, merchant_id: int) -> Merchant | None:
    return await session.get(Merchant, merchant_id)


# 审计快照字段：改密/资金/状态类变更必须可追溯（密码本身不进快照，只记动作）
@router.post("/list", dependencies=[Depends(PermissionChecker("merchant.view"))])
async def merchant_list(session: SessionDep, page: int = Query(1, ge=1),
                        per_page: int = Query(20, ge=1, le=100), body: dict | None = None):
    body = dict(body or {})
    # DataList 把 page/per_page 放在 body 里，非法值 400（带字段名）而不是静默回落
    page = parse_int(body.get("page", page), "page", default=page, minimum=1)
    per_page = parse_int(body.get("per_page", per_page), "per_page",
                         default=per_page, minimum=1, maximum=100)

    query = _build_query(body)
    count_query = select(func.count()).select_from(query.subquery())
    total = (await session.exec(count_query)).one()
    rows = (await session.exec(query.offset((page - 1) * per_page).limit(per_page))).all()
    items = [_dump_merchant(m) for m in rows]
    return suc(paginate(
        items=items, total=total,
        page=page, per_page=per_page,
        has_more_pages=page * per_page < total, has_pages=total > per_page,
    ))


@router.get("/export", dependencies=[Depends(PermissionChecker("merchant.export"))])
async def merchant_export(session: SessionDep, request: Request):
    """导出商户 Excel(xlsx，最多 10000 行）。过滤条件与列表一致（query 参数）。"""
    body = {k: v for k, v in request.query_params.items() if v not in ("", None)}
    query = _build_query(body).limit(10000)
    rows_raw = (await session.exec(query)).all()
    pay_text = {0: "关闭", 1: "正常", 2: "待审核"}
    settle_text = {0: "关闭", 1: "正常"}
    rows = []
    for m in rows_raw:
        rows.append({
            "pid": m.pid,
            "username": m.username,
            "balance": str(m.balance),
            "frozen_balance": str(m.frozen_balance),
            "rate": str(m.rate) if m.rate is not None else "",
            "deduct": ("关" if not m.deduct_enable
                       else f"{m.deduct_frequency}笔" + (f"/满{m.deduct_minmoney}" if m.deduct_minmoney > 0 else "")),
            "email": m.email or "",
            "phone": m.phone or "",
            "qq": m.qq or "",
            "url": m.url or "",
            "settle_account": m.settle_account or "",
            "settle_name": m.settle_name or "",
            "status": "正常" if m.status == 1 else "封禁",
            "pay_status": pay_text.get(m.pay_status, str(m.pay_status)),
            "settle_status": settle_text.get(m.settle_status, str(m.settle_status)),
            "created_at": m.created_at.strftime("%Y-%m-%d %H:%M:%S") if m.created_at else "",
            "last_login_at": m.last_login_at.strftime("%Y-%m-%d %H:%M:%S") if m.last_login_at else "",
        })
    return export_xlsx(
        rows, f"merchants_{datetime.now().strftime('%Y%m%d%H%M%S')}.xlsx",
        columns=["pid", "username", "balance", "frozen_balance", "rate", "deduct",
                 "email", "phone", "qq", "url", "settle_account", "settle_name",
                 "status", "pay_status", "settle_status",
                 "created_at", "last_login_at"],
    )


@router.get("/platform-key", dependencies=[Depends(PermissionChecker("merchant.view"))])
async def merchant_platform_key(session: SessionDep):
    """平台RSA公钥。

    由 settings.platform_rsa_private_key 派生（app.lib.sign.derive_public_key）；
    未配置返回空串。商户用于验证平台 API 响应/异步通知/回跳签名。
    """
    settings = await get_all_settings(session)
    private_raw = str(settings.get("platform_rsa_private_key") or "").strip()
    try:
        return suc({"public_key": derive_public_key(private_raw)})
    except ValueError:
        return err(-1, "平台RSA私钥配置无效，请前往站点配置检查")


@router.get("/{merchant_id}", dependencies=[Depends(PermissionChecker("merchant.view"))])
async def merchant_detail(merchant_id: int, session: SessionDep, request: Request,
                          auth: AdminUser):
    """商户详情（后台「API密钥」弹窗用）。

    比列表接口多两项，仅此接口下发：

    - `api_secret`：MD5 签名密钥**明文**（需求方明确后台不脱敏，运营要整段发给客户）。
      列表接口 `_dump_merchant` 仍只给掩码，避免一次拉全平台密钥。每返回一次明文都
      落一条审计日志（谁 / 何时 / 哪个商户 / 来源 IP），见 `_audit_secret_view`。
    - `default_channel`：平台默认通道（全平台唯一，启用中）的 `{name, payment_type}`，
      没有则为 null；卡片据此决定「默认通道」整行是否展示。
      口径与商户端 `/merchant-api/account/apikey` 完全一致。
    """
    m = await _get_merchant_or_404(session, merchant_id)
    if not m:
        return err(404, "商户不存在")
    data = _dump_merchant(m)
    secret = get_api_secret(m)
    data["api_secret"] = secret or ""
    default = (await session.exec(
        select(Channel)
        .where(col(Channel.is_default).is_(True), col(Channel.status) == 1)
        .order_by(col(Channel.id))
        .limit(1)
    )).first()
    default_type = (await session.get(PaymentType, default.payment_type_id)) if default else None
    data["default_channel"] = (
        {"name": default.name, "payment_type": default_type.name if default_type else ""}
        if default else None
    )
    if secret:
        _audit_secret_view(request, auth, m)
    return suc(data)


def _audit_secret_view(request: Request, auth: AuthResult, m: Merchant) -> None:
    """留痕：后台查看了某商户的明文签名密钥。

    仓库暂无操作审计表（只有登录日志），所以先落到 app.log 的 `app.audit`
    命名 logger：一行一条，带 request_id，可 grep；后续若加 `admin_audit_logs`
    表，改这一处即可。日志本身**不写密钥内容**。
    """
    logger.info(
        "admin_audit action=view_merchant_api_secret admin_id=%s admin=%s "
        "merchant_id=%s pid=%s ip=%s",
        getattr(auth.user, "id", ""),
        getattr(auth.user, "username", ""),
        m.id, m.pid,
        request.client.host if request.client else "",
    )


class CreateMerchantRequest(BaseModel):
    username: str
    password: str
    email: str = ""
    phone: str = ""
    qq: str = ""
    url: str = ""
    rate: str = ""                          # 商户专属费率（%，平台抽成）；留空 = 不占用
    status: int = 1                         # 1=正常 0=封禁
    pay_status: int = 1                     # 0=关闭 1=开启 2=待审核
    settle_status: int = 1                  # 0=关闭 1=开启


@router.post("", dependencies=[Depends(PermissionChecker("merchant.create"))])
async def create_merchant(body: CreateMerchantRequest, session: SessionDep):
    if body.status not in (0, 1):
        return err(-1, "商户状态取值不合法")
    if body.pay_status not in (0, 1, 2):
        return err(-1, "支付状态取值不合法")
    if body.settle_status not in (0, 1):
        return err(-1, "结算状态取值不合法")
    existing = (await session.exec(
        select(Merchant).where(Merchant.username == body.username)
    )).first()
    if existing:
        return err(-1, "用户名已存在")
    # 费率在进重试循环前解析/校验：否则非法输入会被 pid 冲突重试吞成「创建冲突」
    try:
        rate = dmoney(body.rate) if body.rate else None
    except Exception:
        return err(-1, "金额格式错误")
    if rate is not None and (rate < 0 or rate > 100):
        return err(-1, "费率需在 0~100 之间")
    for _ in range(3):
        pid = await generate_merchant_pid(session)
        m = Merchant(
            pid=pid,
            username=body.username,
            password=hash_password(body.password),
            email=body.email or None,
            phone=body.phone or None,
            qq=body.qq or None,
            url=body.url or None,
            rate=rate,
            status=body.status,
            pay_status=body.pay_status,
            settle_status=body.settle_status,
        )
        # 开放 API 默认 MD5：创建即生成密钥（面板可查看/重置）
        set_api_secret(m)
        session.add(m)
        try:
            await session.commit()
            break
        except Exception:
            await session.rollback()
            continue  # 并发 pid 冲突，顺延重试
    else:
        return err(-1, "商户创建冲突，请重试")
    await session.refresh(m)
    return suc(_dump_merchant(m))


class UpdateMerchantRequest(BaseModel):
    password: str = ""
    email: str = ""
    phone: str = ""
    qq: str = ""
    url: str = ""
    allowed_domains: str = ""
    rate: Optional[str] = None              # None=不改；""=清除专属费率；"0"~"100"=设置
    status: Optional[int] = None
    pay_status: Optional[int] = None       # 0=关闭 1=正常 2=待审核
    settle_status: Optional[int] = None    # 0=关闭 1=正常
    default_notify_url: str = ""
    default_return_url: str = ""
    settle_type: Optional[int] = None       # 1=支付宝 2=微信 3=QQ 4=银行卡
    settle_account: str = ""
    settle_name: str = ""
    random_enable: Optional[bool] = None
    random_min: str = ""
    random_max: str = ""
    random_ratelimit: Optional[int] = None
    # 扣量（商户级唯一配置源；无继承语义）
    deduct_enable: Optional[bool] = None
    deduct_frequency: Optional[int] = None
    deduct_minmoney: str = ""
    fixed: Optional[bool] = None


@router.put("/{merchant_id}", dependencies=[Depends(PermissionChecker("merchant.update"))])
async def update_merchant(merchant_id: int, body: UpdateMerchantRequest, session: SessionDep,
                          cache: CacheDep, request: Request, auth: AdminUser):
    m = await _get_merchant_or_404(session, merchant_id)
    if not m:
        return err(404, "商户不存在")
    # 金额字段统一在此解析，非法输入返回业务错误而非 500
    try:
        new_rate = dmoney(body.rate) if body.rate else None
        new_random_min = dmoney(body.random_min) if body.random_min != "" else None
        new_random_max = dmoney(body.random_max) if body.random_max != "" else None
        new_deduct_minmoney = dmoney(body.deduct_minmoney) if body.deduct_minmoney != "" else None
    except Exception:
        return err(-1, "金额格式错误")
    password_changed = bool(body.password)
    if password_changed:
        m.password = hash_password(body.password)
    if body.email != "":
        m.email = body.email or None
    if body.phone != "":
        m.phone = body.phone or None
    if body.qq != "":
        m.qq = body.qq or None
    if body.url != "":
        m.url = body.url or None
    if body.allowed_domains != "":
        m.allowed_domains = body.allowed_domains or None
    if new_rate is not None:
        if new_rate < 0 or new_rate > 100:
            return err(-1, "费率需在 0~100 之间")
        m.rate = new_rate
    elif body.rate is not None:
        # 显式传空串 = 清除商户专属费率，回落到通道费率
        m.rate = None
    if body.status is not None:
        if body.status not in (0, 1):
            return err(-1, "商户状态取值不合法")
        m.status = body.status
    if body.pay_status is not None:
        if body.pay_status not in (0, 1, 2):
            return err(-1, "支付状态取值不合法")
        m.pay_status = body.pay_status
    if body.settle_status is not None:
        if body.settle_status not in (0, 1):
            return err(-1, "结算状态取值不合法")
        m.settle_status = body.settle_status
    if body.settle_type is not None:
        if body.settle_type not in (1, 2, 3, 4):
            return err(-1, "结算方式取值不合法")
        m.settle_type = body.settle_type
    if body.default_notify_url != "":
        m.default_notify_url = body.default_notify_url or None
    if body.default_return_url != "":
        m.default_return_url = body.default_return_url or None
    if body.settle_account != "":
        m.settle_account = body.settle_account or None
    if body.settle_name != "":
        m.settle_name = body.settle_name or None
    if body.random_enable is not None:
        m.random_enable = body.random_enable
    if new_random_min is not None:
        m.random_min = new_random_min
    if new_random_max is not None:
        m.random_max = new_random_max
    if body.random_ratelimit is not None:
        m.random_ratelimit = max(int(body.random_ratelimit), 0)
    if body.deduct_frequency is not None and body.deduct_frequency < 0:
        return err(-1, "扣量频次不能为负数")
    if new_deduct_minmoney is not None and new_deduct_minmoney < 0:
        return err(-1, "起扣金额不能为负数")
    # 先算落地值再赋值：开启扣量但频次为 0 = 「开着却永不扣」，运营会误以为生效，保存时直接拦下
    next_enable = m.deduct_enable if body.deduct_enable is None else body.deduct_enable
    next_frequency = m.deduct_frequency if body.deduct_frequency is None else int(body.deduct_frequency)
    touched_deduct = body.deduct_enable is not None or body.deduct_frequency is not None
    if touched_deduct and next_enable and int(next_frequency or 0) <= 0:
        return err(-1, "开启扣量必须填写频次（每 N 笔扣 1）")
    if body.deduct_frequency is not None:
        m.deduct_frequency = int(body.deduct_frequency)
    if new_deduct_minmoney is not None:
        m.deduct_minmoney = new_deduct_minmoney
    if body.deduct_enable is not None:
        m.deduct_enable = body.deduct_enable
    if body.fixed is not None:
        m.fixed = body.fixed
    session.add(m)
    if password_changed:
        # 改密即吊销该商户全部登录令牌：被盗会话不能在改密后继续使用
        await revoke_all_tokens("merchant", m.id, session, cache=cache)
    await session.commit()
    return suc(_dump_merchant(m))


class SetStatusRequest(BaseModel):
    """商户状态开关：type ∈ user/pay/settle/fixed/rsa2。"""
    type: str
    status: int


@router.post("/{merchant_id}/status", dependencies=[Depends(PermissionChecker("merchant.update"))])
async def set_merchant_status(merchant_id: int, body: SetStatusRequest, session: SessionDep,
                              request: Request, auth: AdminUser):
    m = await _get_merchant_or_404(session, merchant_id)
    if not m:
        return err(404, "商户不存在")
    if body.type == "user":
        if body.status not in (0, 1):
            return err(-1, "状态取值不合法")
        m.status = body.status
    elif body.type == "pay":
        if body.status not in (0, 1, 2):
            return err(-1, "支付状态取值不合法")
        m.pay_status = body.status
    elif body.type == "settle":
        if body.status not in (0, 1):
            return err(-1, "结算状态取值不合法")
        m.settle_status = body.status
    elif body.type == "fixed":
        if body.status not in (0, 1):
            return err(-1, "状态取值不合法")
        m.fixed = bool(body.status)
    elif body.type == "rsa2":
        if body.status not in (0, 1):
            return err(-1, "状态取值不合法")
        if body.status == 1 and not (m.public_key or "").strip():
            return err(-1, "商户未配置RSA公钥，不能开启RSA2签名")
        m.rsa2_enable = bool(body.status)
    else:
        return err(-1, "不支持的状态类型")
    session.add(m)
    await session.commit()
    return suc("success")


@router.delete("/{merchant_id}", dependencies=[Depends(PermissionChecker("merchant.delete"))])
async def delete_merchant(merchant_id: int, session: SessionDep, cache: CacheDep,
                          request: Request, auth: AdminUser):
    """删除商户。

    有余额/冻结余额/待处理结算时拒绝删除（防止资金悬空）；
    资金流水与历史订单保留作审计。删除后该商户所有登录令牌立即失效。
    """
    m = await _get_merchant_or_404(session, merchant_id)
    if not m:
        return err(404, "商户不存在")
    if m.balance != 0 or m.frozen_balance != 0:
        return err(-1, "商户还有未结算余额，请先清退余额后再删除")
    pending_settles = (await session.exec(
        select(func.count()).select_from(Settlement).where(
            col(Settlement.merchant_id) == m.id,
            col(Settlement.status) == 0)
    )).one()
    if int(pending_settles) > 0:
        return err(-1, "商户还有待处理的结算单，请先处理")

    deleted = {"pid": m.pid, "username": m.username,
               "balance": str(m.balance)}
    await revoke_all_tokens("merchant", m.id, session, cache=cache)
    await session.delete(m)
    await session.commit()
    return suc("success")


@router.post("/{merchant_id}/rsakey", dependencies=[Depends(PermissionChecker("merchant.resetkey"))])
async def generate_rsa_key(merchant_id: int, session: SessionDep):
    """生成商户 RSA 密钥对。

    公钥直接落库（单行 base64）；私钥仅本次返回，平台不保存。
    """
    m = await _get_merchant_or_404(session, merchant_id)
    if not m:
        return err(404, "商户不存在")
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    public_flat = pem_to_flat(public_pem)
    m.public_key = public_flat
    session.add(m)
    await session.commit()
    return suc({"public_key": public_flat, "private_key": pem_to_flat(private_pem)})


@router.post("/{merchant_id}/apisecret", dependencies=[Depends(PermissionChecker("merchant.resetkey"))])
async def reset_api_secret(merchant_id: int, session: SessionDep):
    """重置商户 MD5 签名密钥（旧密钥立即失效，接入方需同步更换）。"""
    m = await _get_merchant_or_404(session, merchant_id)
    if not m:
        return err(404, "商户不存在")
    secret = set_api_secret(m)
    session.add(m)
    await session.commit()
    return suc({"api_secret": secret})


class ResetPasswordRequest(BaseModel):
    """新密码（明文由管理员指定）。为空时退回随机生成（兼容旧调用）。"""
    password: str = ""


@router.post("/{merchant_id}/resetpwd", dependencies=[Depends(PermissionChecker("merchant.update"))])
async def reset_password(merchant_id: int, session: SessionDep, cache: CacheDep,
                         request: Request, auth: AdminUser,
                         body: ResetPasswordRequest | None = None):
    """重置登录密码：管理员直接指定新密码；未指定时生成 10 位随机密码返回。

    重置即吊销该商户全部登录令牌（P1-10）。
    """
    m = await _get_merchant_or_404(session, merchant_id)
    if not m:
        return err(404, "商户不存在")
    new_password = (body.password if body else "").strip()
    if new_password:
        if len(new_password) < 6:
            return err(-1, "密码至少 6 位")
        if len(new_password) > 64:
            return err(-1, "密码最多 64 位")
    else:
        alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        new_password = "".join(secrets.choice(alphabet) for _ in range(10))
    m.password = hash_password(new_password)
    session.add(m)
    await revoke_all_tokens("merchant", m.id, session, cache=cache)
    await session.commit()
    return suc({"password": new_password})


class AdjustBalanceRequest(BaseModel):
    amount: str       # 正增负减
    remark: str = ""


@router.post("/{merchant_id}/balance", dependencies=[Depends(PermissionChecker("merchant.balance"))])
async def adjust_balance(merchant_id: int, body: AdjustBalanceRequest, session: SessionDep,
                         request: Request, auth: AdminUser):
    """调整余额（必须写流水）。"""
    try:
        amount = dmoney(body.amount)
    except Exception:
        return err(-1, "金额格式错误")
    if amount == 0:
        return err(-1, "金额不能为 0")
    m = await _get_merchant_or_404(session, merchant_id)
    if not m:
        return err(404, "商户不存在")
    try:
        record = await change_merchant_balance(
            session, merchant_id, amount,
            biz_type="adjust", remark=body.remark or "管理员调整",
        )
        await session.commit()
    except ValueError as e:
        return err(-1, str(e))
    return suc({"balance": str(m.balance), "record_id": record.id})


@router.get("/{merchant_id}/records", dependencies=[Depends(PermissionChecker("merchant.view"))])
async def merchant_records(merchant_id: int, session: SessionDep,
                           page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=100)):
    query = select(MerchantRecord).where(
        MerchantRecord.merchant_id == merchant_id
    ).order_by(col(MerchantRecord.id).desc())

    def dump(r: MerchantRecord) -> dict:
        return {
            "id": r.id,
            "biz_type": r.biz_type,
            "amount": str(r.amount),
            "balance_before": str(r.balance_before),
            "balance_after": str(r.balance_after),
            "order_trade_no": r.order_trade_no or "",
            "remark": r.remark or "",
            "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
        }

    from app.responses import paginated_query_async
    return suc(await paginated_query_async(session, query, page, per_page, dump_fn=dump))


# ============================================================
# 随机金额 / 限制拉单
# ============================================================

class SetRandomRequest(BaseModel):
    random_enable: bool
    random_min: str = "0"
    random_max: str = "0"
    # 仅整数金额：关闭时支持两位小数
    random_integer: bool = False


def _apply_random(merchant: Merchant, body: SetRandomRequest) -> str | None:
    try:
        rmin = dmoney(body.random_min) if body.random_min else Decimal("0")
        rmax = dmoney(body.random_max) if body.random_max else Decimal("0")
    except Exception:
        return "金额格式错误"
    if body.random_enable:
        if rmin <= 0 or rmax <= 0:
            return "请填写最小和最大金额"
        if body.random_integer:
            if rmin != rmin.to_integral_value() or rmax != rmax.to_integral_value():
                return "已开启「整数」，请填写整数"
        elif rmin != rmin.quantize(Decimal("0.01")) or rmax != rmax.quantize(Decimal("0.01")):
            return "随机金额最多两位小数"
        if rmin > rmax:
            rmin, rmax = rmax, rmin
    merchant.random_enable = body.random_enable
    merchant.random_min = rmin
    merchant.random_max = rmax
    merchant.random_integer = body.random_integer
    return None


@router.post("/{merchant_id}/random", dependencies=[Depends(PermissionChecker("merchant.update"))])
async def set_merchant_random(merchant_id: int, body: SetRandomRequest, session: SessionDep):
    m = await _get_merchant_or_404(session, merchant_id)
    if not m:
        return err(404, "商户不存在")
    error = _apply_random(m, body)
    if error:
        return err(-1, error)
    session.add(m)
    await session.commit()
    return suc("success")


@router.post("/random/set-all", dependencies=[Depends(PermissionChecker("merchant.update"))])
async def set_all_merchant_random(body: SetRandomRequest, session: SessionDep):
    """批量随机金额：应用到所有未固定的商户。"""
    probe = Merchant()
    error = _apply_random(probe, body)
    if error:
        return err(-1, error)
    merchants = (await session.exec(
        select(Merchant).where(col(Merchant.fixed) == False)  # noqa: E712
    )).all()
    for m in merchants:
        m.random_enable = body.random_enable
        m.random_min = probe.random_min
        m.random_max = probe.random_max
        m.random_integer = probe.random_integer
        session.add(m)
    await session.commit()
    return suc(f"已应用到 {len(merchants)} 个商户")


class SetRatelimitRequest(BaseModel):
    random_ratelimit: int  # 同一付款人 IP 每分钟建单上限，0=不限


@router.post("/{merchant_id}/ratelimit", dependencies=[Depends(PermissionChecker("merchant.update"))])
async def set_merchant_ratelimit(merchant_id: int, body: SetRatelimitRequest, session: SessionDep):
    m = await _get_merchant_or_404(session, merchant_id)
    if not m:
        return err(404, "商户不存在")
    m.random_ratelimit = max(int(body.random_ratelimit), 0)
    session.add(m)
    await session.commit()
    return suc("success")


# ============================================================
# 扣量（商户级唯一配置源；没有全局参数，急停 = 批量设 0）
# ============================================================

class SetDeductRequest(BaseModel):
    deduct_enable: bool
    deduct_frequency: Optional[int] = None   # 每 N 笔扣 1，开启时必填；不传 = 保留原值
    deduct_minmoney: Optional[str] = None    # 起扣金额；不传 = 保留原值，传 0/空 = 不限金额


def _apply_deduct(m: Merchant, body: SetDeductRequest) -> str | None:
    # 未传金额 = 不动（只改开关不该清掉起扣金额）；显式传 0/空 = 不限金额
    min_money: Decimal | None = None
    if body.deduct_minmoney is not None:
        try:
            min_money = dmoney(body.deduct_minmoney) if body.deduct_minmoney else Decimal("0")
        except Exception:
            return "金额格式错误"
        if min_money < 0:
            return "起扣金额不能为负数"
    frequency = None if body.deduct_frequency is None else int(body.deduct_frequency)
    if frequency is not None and frequency < 0:
        return "扣量频次不能为负数"
    # 停用不清配置：不传频次时保留原值，恢复时不用凭记忆重配
    effective = frequency if frequency is not None else int(m.deduct_frequency or 0)
    if body.deduct_enable and effective <= 0:
        # 开着却 frequency=0 会「永不扣」，运营以为生效，必须拦下
        return "开启扣量必须填写频次（每 N 笔扣 1）"
    m.deduct_enable = body.deduct_enable
    if frequency is not None:
        m.deduct_frequency = frequency
    if min_money is not None:
        m.deduct_minmoney = min_money
    return None


@router.post("/{merchant_id}/deduct", dependencies=[Depends(PermissionChecker("merchant.update"))])
async def set_merchant_deduct(merchant_id: int, body: SetDeductRequest, session: SessionDep):
    """单商户扣量配置：开关 + 频次 + 起扣金额（起扣按 order.money，单笔 >= 才扣）。"""
    m = await _get_merchant_or_404(session, merchant_id)
    if not m:
        return err(404, "商户不存在")
    error = _apply_deduct(m, body)
    if error:
        return err(-1, error)
    session.add(m)
    await session.commit()
    return suc("success")


class SetAllDeductRequest(BaseModel):
    """批量扣量：开启时同时下发频次与起扣金额（金额留空 = 0，不限金额）。

    关闭不传任何数值：只写开关位，两个配置项原样保留，恢复时不用凭记忆重配。
    """
    deduct_enable: bool
    deduct_frequency: Optional[int] = None
    deduct_minmoney: Optional[str] = None


@router.post("/deduct/set-all", dependencies=[Depends(PermissionChecker("merchant.update"))])
async def set_all_merchant_deduct(body: SetAllDeductRequest, session: SessionDep):
    """批量扣量开关：作用于所有未锁定商户，配置锁定的商户不受影响。

    急停（批量关）也不再覆盖锁定商户，锁定的要先去单商户解锁再单独处理。
    关：只写开关位，频次与起扣金额原样保留，恢复时不用凭记忆重配。
    开：频次 + 起扣金额一起下发（金额留空按 0 处理，与单商户表单一致）。
    """
    frequency = int(body.deduct_frequency or 0)
    if frequency < 0:
        return err(-1, "扣量频次不能为负数")
    if body.deduct_enable and frequency <= 0:
        return err(-1, "开启扣量必须填写频次（每 N 笔扣 1）")
    min_money = Decimal("0")
    if body.deduct_enable:
        try:
            min_money = dmoney(body.deduct_minmoney) if body.deduct_minmoney else Decimal("0")
        except Exception:
            return err(-1, "金额格式错误")
        if min_money < 0:
            return err(-1, "起扣金额不能为负数")
    merchants = (await session.exec(
        select(Merchant).where(col(Merchant.fixed) == False)  # noqa: E712
    )).all()
    for m in merchants:
        m.deduct_enable = body.deduct_enable
        if body.deduct_enable:
            m.deduct_frequency = frequency
            m.deduct_minmoney = min_money
        session.add(m)
    await session.commit()
    return suc(f"已应用到 {len(merchants)} 个商户")


# ============================================================
# SSO 一键登录商户后台
# ============================================================

@router.post("/{merchant_id}/sso", dependencies=[Depends(PermissionChecker("merchant.sso"))])
async def merchant_sso(merchant_id: int, session: SessionDep, cache: CacheDep):
    """生成一次性登录凭证（60 秒有效），前端凭 token 到商户端换取正式 JWT。"""
    m = await _get_merchant_or_404(session, merchant_id)
    if not m:
        return err(404, "商户不存在")
    if m.status != 1:
        return err(-1, "商户已被禁用，无法登录")
    token = secrets.token_hex(16)
    try:
        await cache.set(f"sso:merchant:{token}", str(m.id), ex=60)
    except Exception:
        return err(-1, "缓存服务不可用，无法生成登录凭证")
    # 商户中心挂在 /merchant/（站点根 / 是宣传首页，落到根拿不到商户端 SPA）
    return suc({"url": f"/merchant/#/sso-login?token={token}"})
