"""收款页短码（/p/{code}）— 随机短码 + pay_tokens 表映射。

短码为 10 字符随机 base36（小写字母 + 数字，约 52 bit），唯一索引承载；
URL 不携带 pid/agent_id，服务端反查映射行。惰性创建：面板首次取链接时
生成并落库，终身复用（重置 = 换码）。并发首建撞唯一约束时回滚重取，
不做前置锁。

只用小写：MySQL 侧 token 列是 utf8mb4_unicode_ci（大小写不敏感），
混合大小写既拿不到额外唯一性、又会让「能不能命中」取决于库的排序规则，
手打/口播也容易大小写写错。解析时精确匹配落空后按 LOWER() 再查一次，
兼容历史遗留码、也不怕用户手打成大写。
"""
import secrets

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select

from app.models import PayToken

_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
_LEN = 10


def _gen() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(_LEN))


async def _fetch(session, merchant_id: int, agent_id: int) -> PayToken | None:
    return (await session.exec(
        select(PayToken).where(
            PayToken.merchant_id == merchant_id, PayToken.agent_id == agent_id)
    )).first()


async def get_or_create(session, merchant_id: int, agent_id: int = 0) -> str:
    """(merchant_id, agent_id) → 短码；不存在则生成落库。"""
    row = await _fetch(session, merchant_id, agent_id)
    if row:
        return row.token
    for _ in range(3):
        row = PayToken(token=_gen(), merchant_id=merchant_id, agent_id=agent_id)
        session.add(row)
        try:
            await session.commit()
            return row.token
        except IntegrityError:
            # 并发首建撞 (merchant, agent) 或 token 唯一约束：回滚重取
            await session.rollback()
            existed = await _fetch(session, merchant_id, agent_id)
            if existed:
                return existed.token
    raise RuntimeError("短码生成冲突，请重试")


async def get_or_create_many(session, pairs: list[tuple[int, int]]) -> dict[tuple[int, int], str]:
    """批量取短码（代理列表一次性补齐缺失行），返回 {(merchant_id, agent_id): token}。"""
    result: dict[tuple[int, int], str] = {}
    missing: list[tuple[int, int]] = []
    for mid, aid in dict.fromkeys(pairs):
        row = await _fetch(session, mid, aid)
        if row:
            result[(mid, aid)] = row.token
        else:
            missing.append((mid, aid))
    if missing:
        rows = [PayToken(token=_gen(), merchant_id=mid, agent_id=aid) for mid, aid in missing]
        session.add_all(rows)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return {p: await get_or_create(session, p[0], p[1]) for p in pairs}
        result.update({(r.merchant_id, r.agent_id): r.token for r in rows})
    return result


async def resolve(session, token: str) -> PayToken | None:
    """短码 → 映射行；不存在返回 None。

    新码全小写，这里先按原样精确匹配（走唯一索引），未命中再做一次
    大小写无关匹配：库里可能还留着旧 base62 混合大小写码，用户把链接
    手打成大写/全小写都不该打不开。
    """
    if not token or len(token) > 32 or not token.isascii() or not token.isalnum():
        return None
    row = (await session.exec(
        select(PayToken).where(col(PayToken.token) == token)
    )).first()
    if row:
        return row
    # 精确匹配落空才走 LOWER()（不依赖库的排序规则；MySQL 的 CI 排序规则下
    # 上面那条其实已经大小写不敏感，这里主要保 SQLite/binary 库的兼容）
    return (await session.exec(
        select(PayToken).where(func.lower(col(PayToken.token)) == token.lower())
    )).first()
