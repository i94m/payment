"""商户端站内消息：列表 / 未读数 / 已读。"""
from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlmodel import col, func, select

from app.core.deps import SessionDep
from app.core.security import MerchantUser
from app.models import MerchantMessage
from app.responses import err, paginate, suc

router = APIRouter()

# 站内消息业务类型中文名（services/message.send_merchant_message 的 biz_type 取值）
MESSAGE_BIZ_TYPES = {
    "system": "系统通知",
    "settle": "结算",
    "recharge": "充值",
    "order": "订单",
    "refund": "退款",
    "agent": "代理",
}


def _dump(m: MerchantMessage) -> dict:
    return {
        "id": m.id,
        "title": m.title,
        "content": m.content,
        "biz_type": m.biz_type,
        "biz_type_name": MESSAGE_BIZ_TYPES.get(m.biz_type, m.biz_type),
        "is_read": m.read_at is not None,
        "created_at": m.created_at.strftime("%Y-%m-%d %H:%M:%S") if m.created_at else "",
    }


@router.get("/list")
async def message_list(session: SessionDep, auth: MerchantUser,
                       page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=100),
                       unread: int = 0):
    query = select(MerchantMessage).where(
        MerchantMessage.merchant_id == auth.user.id
    ).order_by(col(MerchantMessage.id).desc())
    if unread:
        query = query.where(col(MerchantMessage.read_at).is_(None))
    total = (await session.exec(select(func.count()).select_from(query.subquery()))).one()
    items = (await session.exec(query.offset((page - 1) * per_page).limit(per_page))).all()
    return suc(paginate(items=[_dump(m) for m in items], total=total,
                        page=page, per_page=per_page,
                        has_more_pages=page * per_page < total, has_pages=total > per_page))


@router.get("/unread-count")
async def unread_count(session: SessionDep, auth: MerchantUser):
    cnt = (await session.exec(
        select(func.count()).select_from(MerchantMessage).where(
            MerchantMessage.merchant_id == auth.user.id,
            col(MerchantMessage.read_at).is_(None),
        )
    )).one()
    return suc({"count": int(cnt)})


class ReadRequest(BaseModel):
    id: int = 0  # 0=全部已读


@router.post("/read")
async def mark_read(body: ReadRequest, session: SessionDep, auth: MerchantUser):
    now = datetime.now()
    if body.id > 0:
        msg = await session.get(MerchantMessage, body.id)
        if not msg or msg.merchant_id != auth.user.id:
            return err(404, "消息不存在")
        if msg.read_at is None:
            msg.read_at = now
            session.add(msg)
    else:
        rows = (await session.exec(
            select(MerchantMessage).where(
                MerchantMessage.merchant_id == auth.user.id,
                col(MerchantMessage.read_at).is_(None),
            )
        )).all()
        for msg in rows:
            msg.read_at = now
            session.add(msg)
    await session.commit()
    return suc("success")
