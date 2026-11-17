"""admin 资金明细：全商户流水多条件查询 + 汇总（增加/减少/总计）+ Excel 导出。

对齐旧版 admin/record.php：搜索（操作类型/变更金额/关联订单号）+ 商户号 + 时间范围，
列表带 变更前/变更后 余额，汇总替代旧版弹窗统计。
"""
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import case
from sqlmodel import col, func, select

from app.core.deps import SessionDep
from app.core.security import PermissionChecker
from app.lib.export import export_xlsx
from app.models import Merchant, MerchantRecord
from app.responses import paginated_query_async, suc
from app.routers.common.params import parse_int
from app.routers.merchant.record import BIZ_TYPES

router = APIRouter()


def _build_query(body: dict):
    query = select(MerchantRecord).order_by(col(MerchantRecord.id).desc())
    # 商户列表「资金明细」快捷跳转：/records?merchant_id=xx
    if body.get("merchant_id"):
        query = query.where(MerchantRecord.merchant_id == parse_int(body["merchant_id"], "merchant_id"))
    if body.get("biz_type"):
        query = query.where(MerchantRecord.biz_type == body["biz_type"])
    keyword = str(body.get("keyword") or "").strip()
    if keyword and body.get("search_field"):
        field = str(body["search_field"])
        if field == "amount":
            query = query.where(MerchantRecord.amount == keyword)
        elif field == "order_trade_no":
            query = query.where(col(MerchantRecord.order_trade_no).contains(keyword))
        elif field == "biz_type":
            query = query.where(col(MerchantRecord.biz_type).contains(keyword))
    addtime = body.get("addtime") or ""
    if "," in addtime:
        start, end = addtime.split(",", 1)
        if start.strip():
            query = query.where(col(MerchantRecord.created_at) >= start.strip())
        if end.strip():
            query = query.where(col(MerchantRecord.created_at) <= end.strip() + " 23:59:59")
    return query


def _dump_record(r: MerchantRecord) -> dict:
    return {
        "id": r.id,
        "merchant_id": r.merchant_id,
        "biz_type": r.biz_type,
        "biz_type_name": BIZ_TYPES.get(r.biz_type, r.biz_type),
        "amount": str(r.amount),
        "balance_before": str(r.balance_before),
        "balance_after": str(r.balance_after),
        "order_trade_no": r.order_trade_no or "",
        "remark": r.remark or "",
        "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
    }


async def _merchant_map(session: SessionDep, merchant_ids: set[int]) -> dict[int, Merchant]:
    if not merchant_ids:
        return {}
    merchants = (await session.exec(
        select(Merchant).where(col(Merchant.id).in_(merchant_ids))
    )).all()
    return {m.id: m for m in merchants}


@router.post("/list", dependencies=[Depends(PermissionChecker("merchant.view"))])
async def record_list(session: SessionDep, page: int = Query(1, ge=1),
                      per_page: int = Query(20, ge=1, le=100), body: dict | None = None):
    body = body or {}
    # DataList 把 page/per_page 放在 body 里，此处兜底兼容
    page = parse_int(body.get("page", page), "page", default=page, minimum=1)
    per_page = parse_int(body.get("per_page", per_page), "per_page",
                         default=per_page, minimum=1, maximum=100)
    query = _build_query(body)
    data = await paginated_query_async(session, query, page, per_page, dump_fn=_dump_record)
    # 补商户号/用户名
    merchants = await _merchant_map(session, {it["merchant_id"] for it in data["items"]})
    for it in data["items"]:
        m = merchants.get(it["merchant_id"])
        it["merchant_pid"] = m.pid if m else ""
        it["merchant_username"] = m.username if m else ""
    # 汇总：增加 / 减少 / 总计（对齐旧版 record_stats 弹窗口径）
    # 注意引用 subq.c.amount，直接引用实体列会产生笛卡尔积
    subq = query.subquery()
    amount_col = subq.c.amount
    inc, dec, total = (await session.exec(
        select(
            func.coalesce(func.sum(case((amount_col > 0, amount_col), else_=0)), 0),
            func.coalesce(func.sum(case((amount_col < 0, amount_col), else_=0)), 0),
            func.coalesce(func.sum(amount_col), 0),
        ).select_from(subq)
    )).one()
    data["summary"] = {
        "inc": str(Decimal(str(inc))),
        "dec": str(Decimal(str(dec))),
        "total": str(Decimal(str(total))),
    }
    return suc(data)


@router.get("/export", dependencies=[Depends(PermissionChecker("merchant.export"))])
async def record_export(session: SessionDep, request: Request):
    """导出 Excel(xlsx，最多 10000 行）。过滤条件来自 query 参数，与列表一致。"""
    body = {k: v for k, v in request.query_params.items() if v not in ("", None)}
    query = _build_query(body).limit(10000)
    records = (await session.exec(query)).all()
    merchants = await _merchant_map(session, {r.merchant_id for r in records})

    rows = []
    for r in records:
        m = merchants.get(r.merchant_id)
        rows.append({
            "merchant_pid": m.pid if m else "",
            "merchant_username": m.username if m else "",
            "biz_type": BIZ_TYPES.get(r.biz_type, r.biz_type),
            "amount": str(r.amount),
            "balance_before": str(r.balance_before),
            "balance_after": str(r.balance_after),
            "order_trade_no": r.order_trade_no or "",
            "remark": r.remark or "",
            "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
        })
    return export_xlsx(rows, f"records_{datetime.now().strftime('%Y%m%d%H%M%S')}.xlsx",
                      columns=[
                          ("merchant_pid", "商户号"), ("merchant_username", "用户名"),
                          ("biz_type", "操作类型"), ("amount", "变更金额"),
                          ("balance_before", "变更前金额"), ("balance_after", "变更后金额"),
                          ("order_trade_no", "关联订单号"), ("remark", "备注"),
                          ("created_at", "时间"),
                      ])
