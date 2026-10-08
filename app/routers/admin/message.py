"""后台通知中心：列表 / 未读数 / 已读（已读为全局语义，任一管理员操作即生效）。"""
from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlmodel import col, func, select

from app.core.deps import SessionDep
from app.core.security import AdminUser
from app.models import AdminMessage
from app.responses import err, paginate, suc

router = APIRouter()


def _dump(m: AdminMessage) -> dict:
    return {
        "id": m.id,
        "title": m.title,
        "content": m.content,
        "biz_type": m.biz_type,
        "is_read": m.read_at is not None,
        "created_at": m.created_at.strftime("%Y-%m-%d %H:%M:%S") if m.created_at else "",
    }


@router.get("/list")
async def message_list(session: SessionDep, auth: AdminUser,
                       page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=100),
                       unread: int = 0):
    query = select(AdminMessage).order_by(col(AdminMessage.id).desc())
    if unread:
        query = query.where(col(AdminMessage.read_at).is_(None))
    total = (await session.exec(select(func.count()).select_from(query.subquery()))).one()
    items = (await session.exec(query.offset((page - 1) * per_page).limit(per_page))).all()
    return suc(paginate(items=[_dump(m) for m in items], total=total,
                        page=page, per_page=per_page,
                        has_more_pages=page * per_page < total, has_pages=total > per_page))


@router.get("/unread-count")
async def unread_count(session: SessionDep, auth: AdminUser):
    cnt = (await session.exec(
        select(func.count()).select_from(AdminMessage).where(
            col(AdminMessage.read_at).is_(None),
        )
    )).one()
    return suc({"count": int(cnt)})


class ReadRequest(BaseModel):
    id: int = 0  # 0=全部已读


@router.post("/read")
async def mark_read(body: ReadRequest, session: SessionDep, auth: AdminUser):
    now = datetime.now()
    if body.id > 0:
        msg = await session.get(AdminMessage, body.id)
        if not msg:
            return err(404, "消息不存在")
        if msg.read_at is None:
            msg.read_at = now
            session.add(msg)
    else:
        rows = (await session.exec(
            select(AdminMessage).where(col(AdminMessage.read_at).is_(None))
        )).all()
        for msg in rows:
            msg.read_at = now
            session.add(msg)
    await session.commit()
    return suc("success")
