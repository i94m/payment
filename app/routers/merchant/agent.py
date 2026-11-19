"""商户端下级代理：CRUD + 专属收款链接 + 业绩统计 + SSO 代登录（订单归属 agents.id）。

业绩口径：已支付收款单（排除充值单与扣量单），金额一律 Order.money；
今日/昨日按 paid_at 划分（对齐老系统 date 列语义）。
代理名称全局唯一（代理端登录身份）；删除为软删除，历史业绩聚合保留。
整组接口受站点开关 agent_enable 门控（关闭后 403，不只是隐藏菜单）。
"""
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import and_, case, desc, func, or_
from sqlmodel import col, select

from app.core.deps import CacheDep, SessionDep, require_agent_enabled
from app.core.security import MerchantUser, hash_password
from app.lib.linktoken import paypage_url
from app.services import paytoken
from app.models import Agent, Order
from app.responses import err, paginate, suc

router = APIRouter(dependencies=[Depends(require_agent_enabled)])

# 业绩口径：已支付收款单，排除扣量单（商户/代理侧统计均不含被抽单订单）
_PAID_INCOME = (Order.status == 1, Order.order_type == 0, col(Order.is_deducted) == False)  # noqa: E712


def _dump(a: Agent) -> dict:
    return {
        "id": a.id,
        "name": a.name,
        "status": a.status,
        "remark": a.remark or "",
        "created_at": a.created_at.strftime("%Y-%m-%d %H:%M:%S") if a.created_at else "",
        "last_login_at": a.last_login_at.strftime("%Y-%m-%d %H:%M:%S") if a.last_login_at else "",
    }


def _kw_conditions(merchant_id: int, kw: str) -> list:
    """代理列表/业绩分解共用的过滤：商户隔离 + 未删除 + 名称/备注模糊搜索。"""
    conditions = [Agent.merchant_id == merchant_id, col(Agent.deleted_at).is_(None)]
    if kw:
        conditions.append(or_(
            col(Agent.name).contains(kw),
            col(Agent.remark).contains(kw),
        ))
    return conditions


@router.get("/list")
async def agent_list(session: SessionDep, auth: MerchantUser,
                     page: int = Query(1, ge=1), per_page: int = Query(15, ge=1, le=100),
                     kw: str = Query("", max_length=64)):
    rows = (await session.exec(
        select(Agent).where(*_kw_conditions(auth.user.id, kw.strip()))
        .order_by(col(Agent.id).desc())
        .offset((page - 1) * per_page).limit(per_page)
    )).all()
    total = (await session.exec(
        select(func.count()).select_from(Agent).where(*_kw_conditions(auth.user.id, kw.strip()))
    )).one()
    # 专属链接短码一次性补齐（缺失行批量生成），列表页无 N+1 写放大
    codes = await paytoken.get_or_create_many(
        session, [(auth.user.id, a.id) for a in rows])
    items = []
    for a in rows:
        item = _dump(a)
        item["link"] = paypage_url(codes[(auth.user.id, a.id)])
        items.append(item)
    return suc(paginate(items=items, total=total, page=page, per_page=per_page,
                        has_more_pages=page * per_page < total, has_pages=total > per_page))


class AgentRequest(BaseModel):
    name: str
    password: str
    remark: str = ""


@router.post("")
async def create_agent(body: AgentRequest, session: SessionDep, auth: MerchantUser):
    name = body.name.strip()
    if not name or len(name) > 64:
        return err(-1, "代理名称不能为空且不超过 64 字")
    if len(body.password) < 6:
        return err(-1, "代理密码至少 6 位")
    # 全局唯一：任何商户的代理（含历史软删除释放后的名称由删除时改名保证）不可重名
    exists = (await session.exec(
        select(Agent).where(col(Agent.name) == name)
    )).first()
    if exists:
        return err(-1, "代理名称已存在，请更换名称")
    agent = Agent(
        merchant_id=auth.user.id,
        name=name,
        password=hash_password(body.password),
        remark=body.remark[:255] or None,
        status=1,
    )
    session.add(agent)
    await session.commit()
    await session.refresh(agent)
    item = _dump(agent)
    item["link"] = paypage_url(await paytoken.get_or_create(session, auth.user.id, agent.id))
    return suc(item)


class AgentUpdateRequest(BaseModel):
    name: str = ""
    password: str = ""
    remark: str = ""
    status: int | None = None


@router.put("/{agent_id}")
async def update_agent(agent_id: int, body: AgentUpdateRequest, session: SessionDep, auth: MerchantUser):
    agent = await session.get(Agent, agent_id)
    if not agent or agent.merchant_id != auth.user.id or agent.deleted_at is not None:
        return err(404, "代理不存在")
    if body.name != "":
        name = body.name.strip()
        if not name or len(name) > 64:
            return err(-1, "代理名称不能为空且不超过 64 字")
        # 全局唯一：跨商户也不可重名
        exists = (await session.exec(
            select(Agent).where(col(Agent.name) == name, Agent.id != agent.id)
        )).first()
        if exists:
            return err(-1, "代理名称已存在，请更换名称")
        agent.name = name
    if body.password:
        if len(body.password) < 6:
            return err(-1, "代理密码至少 6 位")
        agent.password = hash_password(body.password)
    if body.remark != "":
        agent.remark = body.remark[:255] or None
    if body.status is not None:
        if body.status not in (0, 1):
            return err(-1, "状态取值不合法")
        agent.status = body.status
    session.add(agent)
    await session.commit()
    return suc(_dump(agent))


@router.delete("/{agent_id}")
async def delete_agent(agent_id: int, session: SessionDep, auth: MerchantUser):
    """软删除：status 置 0 拒绝登录/建单/SSO，行保留维持历史订单业绩聚合。

    name 追加 #del{id} 释放原名称供复用（全局唯一约束下）。
    """
    agent = await session.get(Agent, agent_id)
    if not agent or agent.merchant_id != auth.user.id or agent.deleted_at is not None:
        return err(404, "代理不存在")
    agent.status = 0
    agent.deleted_at = datetime.now()
    agent.name = f"{agent.name}#del{agent.id}"[:64]
    session.add(agent)
    await session.commit()
    return suc("success")


# ============================================================
# SSO 一键登录代理面板
# ============================================================

@router.post("/{agent_id}/sso")
async def agent_sso(agent_id: int, session: SessionDep, cache: CacheDep, auth: MerchantUser):
    """生成一次性登录凭证（60 秒有效），前端凭 token 到代理端换取正式 JWT。"""
    agent = await session.get(Agent, agent_id)
    if not agent or agent.merchant_id != auth.user.id:
        return err(404, "代理不存在")
    if agent.status != 1:
        return err(-1, "代理已被禁用，无法登录")
    token = secrets.token_hex(16)
    try:
        await cache.set(f"sso:agent:{token}", str(agent.id), ex=60)
    except Exception:
        return err(-1, "缓存服务不可用，无法生成登录凭证")
    return suc({"url": f"/agent/#/sso-login?token={token}"})


# ============================================================
# 业绩统计
# ============================================================

@router.get("/performance")
async def performance(session: SessionDep, auth: MerchantUser, agent_id: int = 0):
    """代理业绩：今日/昨日/累计 订单数与金额（排除充值单；0=全部代理合计）。"""
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)

    async def _stat(start=None, end=None) -> dict:
        q = select(func.count(), func.coalesce(func.sum(Order.money), 0)).select_from(Order).where(
            *_PAID_INCOME, Order.merchant_id == auth.user.id,
        )
        if agent_id > 0:
            q = q.where(Order.agent_id == agent_id)
        if start is not None:
            q = q.where(Order.paid_at >= start)
        if end is not None:
            q = q.where(Order.paid_at < end)
        cnt, money = (await session.exec(q)).one()
        return {"count": int(cnt), "money": str(money)}

    # 归属校验：指定代理时必须属于当前商户（软删除的代理视为不存在）
    if agent_id > 0:
        agent = await session.get(Agent, agent_id)
        if not agent or agent.merchant_id != auth.user.id or agent.deleted_at is not None:
            return err(404, "代理不存在")

    return suc({
        "today": await _stat(today_start),
        "yesterday": await _stat(yesterday_start, today_start),
        "total": await _stat(),
    })


@router.get("/performance/list")
async def performance_list(session: SessionDep, auth: MerchantUser,
                           page: int = Query(1, ge=1), per_page: int = Query(15, ge=1, le=100),
                           kw: str = Query("", max_length=64)):
    """代理业绩明细：按代理逐行分解（无单代理也出 0 行），summary 为商户整体口径。

    对齐老系统 agentPerformance：明细行 LEFT JOIN 订单聚合、按总金额倒序；
    顶部汇总为该商户全部订单（含无代理订单），不做 agent 过滤。
    """
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)
    kw = kw.strip()

    # 明细行：agents LEFT JOIN 已支付收款单，按代理聚合（业绩口径进 JOIN 条件）
    join_cond = and_(Order.agent_id == Agent.id, *_PAID_INCOME)
    today_money = func.coalesce(func.sum(
        case((Order.paid_at >= today_start, Order.money), else_=0)), 0)
    yesterday_money = func.coalesce(func.sum(
        case((and_(Order.paid_at >= yesterday_start, Order.paid_at < today_start), Order.money), else_=0)), 0)
    total_money = func.coalesce(func.sum(Order.money), 0)
    total_count = func.count(Order.id)

    rows = (await session.exec(
        select(Agent.id, Agent.name, today_money, yesterday_money, total_money, total_count)
        .select_from(Agent)
        .outerjoin(Order, join_cond)
        .where(*_kw_conditions(auth.user.id, kw))
        .group_by(Agent.id, Agent.name)
        .order_by(desc(total_money), desc(Agent.id))
        .offset((page - 1) * per_page).limit(per_page)
    )).all()
    total = (await session.exec(
        select(func.count()).select_from(Agent).where(*_kw_conditions(auth.user.id, kw))
    )).one()

    # 商户整体汇总（老系统顶部四卡口径：全部订单，不限代理归属）
    cnt, s_today, s_yesterday, s_total = (await session.exec(
        select(
            func.count(),
            func.coalesce(func.sum(case((Order.paid_at >= today_start, Order.money), else_=0)), 0),
            func.coalesce(func.sum(
                case((and_(Order.paid_at >= yesterday_start, Order.paid_at < today_start), Order.money), else_=0)), 0),
            func.coalesce(func.sum(Order.money), 0),
        ).where(Order.merchant_id == auth.user.id, *_PAID_INCOME)
    )).one()

    items = [{
        "id": r[0],
        "name": r[1],
        "today_money": str(r[2]),
        "yesterday_money": str(r[3]),
        "total_money": str(r[4]),
        "total_count": int(r[5]),
    } for r in rows]
    return suc(paginate(
        items=items, total=total, page=page, per_page=per_page,
        has_more_pages=page * per_page < total, has_pages=total > per_page,
        summary={
            "today_money": str(s_today),
            "yesterday_money": str(s_yesterday),
            "total_money": str(s_total),
            "total_count": int(cnt),
        },
    ))
