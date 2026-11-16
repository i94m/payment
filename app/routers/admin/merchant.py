"""admin 商户管理：

- CRUD / 启停 / 删除 / 重置密钥 / 余额调整（必写流水）/ 流水查询
- 支付/结算独立开关（pay: 0=关 1=开 2=待审核；settle: 0/1）
- 随机金额（单个/批量，min==max 固定金额）与限制拉单（同 IP 每分钟建单上限）
- 固定保护（fixed）：免受批量随机金额影响
- 精细搜索（按字段）+ 状态筛选 + 排序 + 高级搜索（N 天无订单）+ CSV 导出
- SSO 一键登录商户后台（一次性 token，60 秒有效）
"""
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
from app.core.security import PermissionChecker, hash_password, revoke_all_tokens
from app.lib.export import export_csv
from app.models import Merchant, MerchantRecord, Order, Settlement
from app.responses import err, paginate, suc
from app.routers.common.params import parse_int
from app.services.merchant import generate_merchant_pid
from app.services.balance import change_merchant_balance, dmoney
from app.services.settings import get_all_settings
from app.lib.sign import derive_public_key, pem_to_flat

router = APIRouter()


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


def _dump_merchant(m: Merchant) -> dict:
    return {
        "id": m.id,
        "pid": m.pid,
        "username": m.username,
        "public_key": pem_to_flat(m.public_key or ""),
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
        "random_ratelimit": m.random_ratelimit,
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
    query = select(Merchant)
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
    # 排序
    order = str(body.get("order", "") or "")
    query = query.order_by(_SORTS.get(order, _SORTS[""]))
    return query


async def _get_merchant_or_404(session, merchant_id: int) -> Merchant | None:
    return await session.get(Merchant, merchant_id)


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
    """导出商户 CSV（最多 10000 行）。过滤条件与列表一致（query 参数）。"""
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
    return export_csv(
        rows, f"merchants_{datetime.now().strftime('%Y%m%d%H%M%S')}.csv",
        columns=["pid", "username", "balance", "frozen_balance", "rate",
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
async def merchant_detail(merchant_id: int, session: SessionDep):
    m = await _get_merchant_or_404(session, merchant_id)
    if not m:
        return err(404, "商户不存在")
    return suc(_dump_merchant(m))


class CreateMerchantRequest(BaseModel):
    username: str
    password: str
    email: str = ""
    phone: str = ""
    qq: str = ""
    url: str = ""
    rate: str = ""
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
            rate=dmoney(body.rate) if body.rate else None,
            status=body.status,
            pay_status=body.pay_status,
            settle_status=body.settle_status,
        )
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
    rate: str = ""
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
    fixed: Optional[bool] = None


@router.put("/{merchant_id}", dependencies=[Depends(PermissionChecker("merchant.update"))])
async def update_merchant(merchant_id: int, body: UpdateMerchantRequest, session: SessionDep):
    m = await _get_merchant_or_404(session, merchant_id)
    if not m:
        return err(404, "商户不存在")
    # 金额字段统一在此解析，非法输入返回业务错误而非 500
    try:
        new_rate = dmoney(body.rate) if body.rate != "" else None
        new_random_min = dmoney(body.random_min) if body.random_min != "" else None
        new_random_max = dmoney(body.random_max) if body.random_max != "" else None
    except Exception:
        return err(-1, "金额格式错误")
    if body.password:
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
        m.rate = new_rate
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
    if body.fixed is not None:
        m.fixed = body.fixed
    session.add(m)
    await session.commit()
    return suc(_dump_merchant(m))


class SetStatusRequest(BaseModel):
    """商户状态开关：type ∈ user/pay/settle/fixed。"""
    type: str
    status: int


@router.post("/{merchant_id}/status", dependencies=[Depends(PermissionChecker("merchant.update"))])
async def set_merchant_status(merchant_id: int, body: SetStatusRequest, session: SessionDep):
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
    else:
        return err(-1, "不支持的状态类型")
    session.add(m)
    await session.commit()
    return suc("success")


@router.delete("/{merchant_id}", dependencies=[Depends(PermissionChecker("merchant.delete"))])
async def delete_merchant(merchant_id: int, session: SessionDep, cache: CacheDep):
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


@router.post("/{merchant_id}/resetpwd", dependencies=[Depends(PermissionChecker("merchant.update"))])
async def reset_password(merchant_id: int, session: SessionDep, cache: CacheDep):
    """重置登录密码：生成 10 位随机密码返回一次（商户找回密码的管理侧兜底）。"""
    m = await _get_merchant_or_404(session, merchant_id)
    if not m:
        return err(404, "商户不存在")
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
async def adjust_balance(merchant_id: int, body: AdjustBalanceRequest, session: SessionDep):
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


def _apply_random(merchant: Merchant, body: SetRandomRequest) -> str | None:
    try:
        rmin = dmoney(body.random_min) if body.random_min else Decimal("0")
        rmax = dmoney(body.random_max) if body.random_max else Decimal("0")
    except Exception:
        return "金额格式错误"
    if body.random_enable:
        if rmin <= 0 or rmax <= 0:
            return "请填写最小和最大金额"
        if rmin != rmin.to_integral_value() or rmax != rmax.to_integral_value():
            return "随机金额仅支持整数"
        if rmin > rmax:
            rmin, rmax = rmax, rmin
    merchant.random_enable = body.random_enable
    merchant.random_min = rmin
    merchant.random_max = rmax
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
