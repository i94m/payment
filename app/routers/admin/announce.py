"""admin 公告管理：CRUD。发布中的公告在商户端概览/公告接口展示。"""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlmodel import col, func, select

from app.core.deps import SessionDep
from app.core.security import PermissionChecker
from app.models import Announcement
from app.responses import err, paginate, suc

router = APIRouter()


def _dump(a: Announcement) -> dict:
    return {
        "id": a.id,
        "content": a.content,
        "status": a.status,
        "created_at": a.created_at.strftime("%Y-%m-%d %H:%M:%S") if a.created_at else "",
        "updated_at": a.updated_at.strftime("%Y-%m-%d %H:%M:%S") if a.updated_at else "",
    }


@router.post("/list", dependencies=[Depends(PermissionChecker("announce.view"))])
async def announce_list(session: SessionDep, page: int = Query(1, ge=1),
                        per_page: int = Query(20, ge=1, le=100), body: dict | None = None):
    body = body or {}
    query = select(Announcement).order_by(col(Announcement.id).desc())
    if body.get("keyword"):
        query = query.where(col(Announcement.content).contains(str(body["keyword"])))
    if body.get("status") is not None and body.get("status") != "":
        query = query.where(Announcement.status == int(body["status"]))
    total = (await session.exec(select(func.count()).select_from(query.subquery()))).one()
    items = (await session.exec(query.offset((page - 1) * per_page).limit(per_page))).all()
    return suc(paginate(items=[_dump(a) for a in items], total=total,
                        page=page, per_page=per_page,
                        has_more_pages=page * per_page < total, has_pages=total > per_page))


class AnnounceRequest(BaseModel):
    content: str
    status: int = 1


@router.post("", dependencies=[Depends(PermissionChecker("announce.create"))])
async def create_announce(body: AnnounceRequest, session: SessionDep):
    if not body.content.strip():
        return err(-1, "公告内容不能为空")
    a = Announcement(
        content=body.content,
        status=1 if body.status == 1 else 0,
    )
    session.add(a)
    await session.commit()
    await session.refresh(a)
    return suc(_dump(a))


@router.put("/{announce_id}", dependencies=[Depends(PermissionChecker("announce.update"))])
async def update_announce(announce_id: int, body: AnnounceRequest, session: SessionDep):
    a = await session.get(Announcement, announce_id)
    if not a:
        return err(404, "公告不存在")
    if not body.content.strip():
        return err(-1, "公告内容不能为空")
    a.content = body.content
    a.status = 1 if body.status == 1 else 0
    session.add(a)
    await session.commit()
    return suc(_dump(a))


@router.delete("/{announce_id}", dependencies=[Depends(PermissionChecker("announce.delete"))])
async def delete_announce(announce_id: int, session: SessionDep):
    a = await session.get(Announcement, announce_id)
    if not a:
        return err(404, "公告不存在")
    await session.delete(a)
    await session.commit()
    return suc("success")
