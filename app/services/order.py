"""订单生命周期：建单 → 拉起支付 → 回调幂等记账。

纪律：
- 金额全链路 Decimal + quantize(0.01)，严禁 float
- 建单事务内无任何外部 IO（httpx 只出现在事务外）
- 幂等三道防线：唯一索引 (out_trade_no, merchant_id) / 状态机行锁 0→1 / Redis 锁
- 插件只做协议；金额业务比对在这里统一执行
"""
import logging
import random
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select as sa_select
from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.cache import CacheClient, try_lock, release_lock
from app.models import Counter, Merchant, Order, PaymentType
from app.services.balance import change_merchant_balance, dmoney
from app.services.pay_debug import write_pay_debug
from app.services.channel import ResolvedChannel, choose_channel
from app.services.plugin import PluginRegistry, maybe_await
from app.services.plugin_interfaces import PaymentContext, PaymentResult
from app.constants.cache_keys import CacheKeys
from app.services.settings import get_int_setting
from app.lib.sign import make_rsa_sign

_log = logging.getLogger(__name__)

# 订单状态
ORDER_PENDING = 0
ORDER_PAID = 1
ORDER_REFUNDED = 2
ORDER_EXPIRED = 3

CENT = Decimal("0.01")


def generate_trade_no() -> str:
    """订单号：yymmddHHMMSSffffff(18) + 4 位随机 = 22 位（时间前缀保证趋势递增，利于聚簇索引）。

    微秒级时间戳：秒级+6位随机在 ~370单/s 压测下实测撞出唯一键冲突
    （同秒随机空间仅 1e6），扩到微秒后撞号需同微秒且 4 位随机相同。
    """
    return datetime.now().strftime("%y%m%d%H%M%S%f") + str(random.randint(1000, 9999))


async def get_order_by_trade_no(session: AsyncSession, trade_no: str) -> Order | None:
    """按业务键取订单（trade_no 唯一索引）。"""
    if not trade_no:
        return None
    return (await session.exec(select(Order).where(Order.trade_no == trade_no))).first()


def fmt_money(v) -> str:
    """money 序列化：固定两位小数（5 → 5.00，5.1 → 5.10）。"""
    return format(Decimal(str(v)).quantize(CENT), "f")


def order_remaining_seconds(order: Order) -> int:
    """支付链接剩余有效秒数：以订单 expire_at 为准。

    插件/流程结果里的 expires_in 只是上游会话参考值，对外响应一律用
    订单真实有效期（order_expire_minutes 设置），避免商户拿到误导性的
    固定 300 秒。
    """
    if order.expire_at is None:
        return 0
    return max(0, int((order.expire_at - datetime.now()).total_seconds()))


@dataclass
class PayFlowResult:
    ok: bool
    pay_type: str = ""    # jump/qrcode/html/scheme
    pay_info: str = ""    # 跳转地址 / 二维码内容 / HTML
    expires_in: int = 300
    error: str = ""
    plugin: Any = None    # 实际执行的插件实例（其 last_debug 可供排障展示）


@dataclass
class PaidResult:
    status: str           # paid/duplicate/mismatch/invalid/not_found/lock_failed
    order: Order | None = None
    should_notify: bool = False


def _quant(v: Decimal | str | None) -> Decimal:
    return dmoney(v if v is not None else 0)


def _host_allowed(allowed_domains: str | None, host: str) -> bool:
    """支付域名白名单校验：空=不限；支持 *.example.com 通配。"""
    host = (host or "").strip().lower()
    if not allowed_domains:
        return True
    if not host:
        return False
    for raw in allowed_domains.split(","):
        rule = raw.strip().lower().lstrip(".")
        if not rule:
            continue
        if rule.startswith("*."):
            suffix = rule[1:]  # .example.com
            if host.endswith(suffix) and host != suffix[1:]:
                return True
        elif host == rule:
            return True
    return False


# ============================================================
# 建单
# ============================================================

async def create_order(params: dict, session: AsyncSession, cache: CacheClient | None = None,
                       order_type: int = 0, agent_id: int = 0, request_host: str = "") -> Order:
    """建单（幂等）。同一商户同一 out_trade_no 返回已有订单。

    事务内无外部 IO。抛 ValueError 表示业务校验失败。
    order_type: 0=普通收款 1=余额充值（仅内部入口可传，开放 API 固定为 0）。
    agent_id:   归属代理（须为该商户名下启用代理，0=无）。
    request_host: 来源域名（支付域名白名单 enforcement，空=不校验）。
    """
    pid = params.get("pid", "")
    out_trade_no = str(params.get("out_trade_no", "") or "").strip()
    subject = str(params.get("name", "") or params.get("subject", "") or "").strip()
    type_code = str(params.get("type", "") or "").strip()
    notify_url = str(params.get("notify_url", "") or "").strip()
    return_url = str(params.get("return_url", "") or "").strip()
    param = str(params.get("param", "") or "").strip()

    if not pid:
        raise ValueError("商户ID不能为空")
    if not out_trade_no:
        raise ValueError("商户订单号不能为空")
    if len(out_trade_no) > 64:
        raise ValueError("商户订单号过长")
    if not subject:
        raise ValueError("商品名称不能为空")
    if not type_code:
        raise ValueError("支付方式不能为空")

    merchant = (await session.exec(
        select(Merchant).where(Merchant.pid == int(pid))
    )).first()
    if not merchant:
        raise ValueError("商户不存在")
    if merchant.status != 1:
        raise ValueError("商户已被封禁")
    if merchant.pay_status == 0:
        raise ValueError("商户支付权限已关闭")
    if merchant.pay_status == 2:
        raise ValueError("商户支付权限待审核，暂不能支付")

    # 支付域名白名单：配置了白名单时来源域名必须命中
    if not _host_allowed(merchant.allowed_domains, request_host):
        raise ValueError("来源域名未授权，请检查支付域名白名单")

    # 代理归属：仅接受该商户名下启用中的代理
    if agent_id > 0:
        from app.models import Agent
        agent = (await session.exec(
            select(Agent).where(
                col(Agent.id) == agent_id,
                col(Agent.merchant_id) == merchant.id,
                col(Agent.status) == 1,
            )
        )).first()
        if not agent:
            agent_id = 0

    raw_money = str(params.get("money", "") or "").strip()
    try:
        money = dmoney(Decimal(raw_money))
    except Exception:
        raise ValueError("金额格式错误")
    if money <= 0:
        raise ValueError("金额必须大于0")

    # 限制拉单：同一付款人 IP 1 分钟内对该商户的
    # 建单数超限则拒绝，防收款链接被爬虫/脚本刷单，0=不限制
    ratelimit = int(merchant.random_ratelimit or 0)
    client_ip = str(params.get("client_ip", "") or "")
    if ratelimit > 0 and client_ip:
        minute_ago = datetime.now() - timedelta(seconds=60)
        pulled = (await session.exec(
            select(func.count()).select_from(Order).where(
                col(Order.merchant_id) == merchant.id,
                col(Order.client_ip) == client_ip,
                col(Order.created_at) >= minute_ago,
            )
        )).one()
        if int(pulled) >= ratelimit:
            raise ValueError("请求过于频繁，请稍后再试")

    pay_type = (await session.exec(
        select(PaymentType).where(PaymentType.code == type_code, PaymentType.status == 1)
    )).first()
    if not pay_type:
        raise ValueError("支付方式不存在或未启用")

    # 幂等：同商户同 out_trade_no 直接返回已有订单
    existing = (await session.exec(
        select(Order).where(Order.out_trade_no == out_trade_no, Order.merchant_id == merchant.id)
    )).first()
    if existing:
        return existing

    expire_minutes = await get_int_setting(session, "order_expire_minutes", cache)
    if expire_minutes <= 0:
        expire_minutes = 30

    order = Order(
        trade_no=generate_trade_no(),
        merchant_id=merchant.id,
        out_trade_no=out_trade_no,
        payment_type_id=pay_type.id,
        payment_type_code=pay_type.code,
        subject=subject[:128],
        money=money,
        notify_url=notify_url[:500] or merchant.default_notify_url,
        return_url=return_url[:500] or merchant.default_return_url,
        param=param[:500],
        client_ip=str(params.get("client_ip", "") or "")[:45],
        agent_id=agent_id,
        order_type=order_type,
        status=ORDER_PENDING,
        created_at=datetime.now(),  # 应用侧本地时间（与 paid_at/expire_at 一致；DB 默认值在 SQLite 为 UTC）
        expire_at=datetime.now() + timedelta(minutes=expire_minutes),
    )
    session.add(order)
    # rollback 会使会话内全部 ORM 实例过期：冲突处理路径里再访问 merchant.id /
    # pay_type.code 会触发同步惰性加载（async 下抛 MissingGreenlet，压测实测 500）。
    # 提前捕获为本地变量，冲突路径不再触碰实例属性。
    merchant_id = merchant.id
    pay_type_code = pay_type.code
    for _attempt in range(3):
        try:
            await session.commit()
            break
        except Exception as e:
            # 并发冲突处理：
            # 1) (out_trade_no, merchant_id) 冲突 → 幂等返回已有订单
            # 2) trade_no 撞号 → 换单号重试
            # 注意：驱动可能把唯一键冲突包装成 MissingGreenlet 等驱动级异常
            # （压测实测），不能只按 IntegrityError 识别，一律走冲突处理路径
            _log.warning("建单提交冲突（第 %s 次）out_trade_no=%s: %s",
                         _attempt + 1, out_trade_no, type(e).__name__)
            try:
                await session.rollback()
            except Exception:
                # 会话已不可用（连接级异常二次抛出）：不再重试，交由上层报可重试错误
                raise ValueError("订单提交冲突，请重试") from e
            existing = (await session.exec(
                select(Order).where(Order.out_trade_no == out_trade_no, Order.merchant_id == merchant_id)
            )).first()
            if existing:
                if existing.money != money or existing.payment_type_code != pay_type_code:
                    raise ValueError("out_trade_no 已存在且金额或支付方式不一致")
                # rollback 同样过期了调用方持有的 merchant（同会话实例），
                # 刷新后再返回，避免下游读属性触发惰性加载
                await session.refresh(merchant)
                return existing
            order.trade_no = generate_trade_no()
            session.add(order)
    else:
        raise ValueError("订单号生成冲突，请重试")
    await session.refresh(order)
    if _attempt > 0:
        # 经历过冲突重试：merchant 已被 rollback 过期，刷新供下游（费率/随机金额）读取
        await session.refresh(merchant)
    return order


# ============================================================
# 拉起支付（选通道 + 金额计算 + 插件下单）
# ============================================================

def compute_amounts(realmoney: Decimal, merchant_rate: Decimal, cost_rate: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    """金额计算：到账 = 实付 * (100-商户费率)/100；成本 = 实付 * 成本费率/100。"""
    realmoney = _quant(realmoney)
    merchant_income = (realmoney * (Decimal(100) - merchant_rate) / Decimal(100)).quantize(CENT)
    channel_cost = (realmoney * cost_rate / Decimal(100)).quantize(CENT)
    profit = (realmoney - merchant_income - channel_cost).quantize(CENT)
    return merchant_income, channel_cost, profit


def build_payment_context(
    order: Order,
    plugin_name: str,
    notify_base: str,
    return_base: str,
    site_url: str = "",
    is_mobile: bool = False,
) -> PaymentContext:
    """构建插件下单上下文（回调地址含插件名：/callbacks/{plugin}/{trade_no}）。"""
    base = notify_base.rstrip("/")
    return PaymentContext(
        order={
            "trade_no": order.trade_no,
            "out_trade_no": order.out_trade_no,
            "subject": order.subject,
            "money": str(order.money),
            "realmoney": str(order.realmoney) if order.realmoney else str(order.money),
            "payment_type_code": order.payment_type_code,
            "merchant_id": order.merchant_id,
        },
        notify_url=f"{base}/callbacks/{plugin_name}/{order.trade_no}",
        return_url=f"{return_base.rstrip('/')}/callbacks/{plugin_name}/{order.trade_no}/return",
        site_url=site_url,
        is_mobile=is_mobile,
    )


def _dbg_submit(channel, order: Order, stage: str, ctx: PaymentContext | None = None,
                is_mobile: bool = False, result: dict | None = None,
                error: str | None = None, plugin: Any = None) -> None:
    """下单环节调试日志（通道 debug_log 开启时写 /tmp/pay_日期_插件.log）。

    严禁带入通道凭证（resolved.config）。
    """
    if not getattr(channel, "debug_log", 0):
        return
    fields: dict = {
        "trade_no": order.trade_no,
        "channel_id": channel.id,
        "stage": stage,
        "ctx": {
            "order": ctx.order if ctx else None,
            "notify_url": ctx.notify_url if ctx else "",
            "is_mobile": is_mobile,
        },
    }
    if result is not None:
        fields["result"] = result
    if error is not None:
        fields["error"] = error
    fields["upstream"] = getattr(plugin, "last_debug", None)
    write_pay_debug(channel.plugin_name, "SUBMIT", fields)


async def process_payment(
    order: Order,
    merchant: Merchant,
    session: AsyncSession,
    cache: CacheClient | None = None,
    is_mobile: bool = False,
    notify_base: str = "",
    return_base: str = "",
    site_url: str = "",
    channel_id: int | None = None,
) -> PayFlowResult:
    """拉起支付：选通道 → 落金额与通道信息（事务）→ 插件下单（事务外）→ 存 payurl。

    channel_id 指定时跳过通道路由、强制走该通道（管理端通道测试用）。
    上游报错时订单保持待支付（trade_no 保留），返回 ok=False。
    """
    resolved: ResolvedChannel | None = None
    if channel_id:
        # 指定通道（管理端通道测试）：跳过路由，直接解析该通道
        from app.services.channel import get_channel_with_config
        ch, ch_config = await get_channel_with_config(session, channel_id)
        if not ch:
            return PayFlowResult(ok=False, error="支付通道不存在")
        resolved = ResolvedChannel(
            channel=ch, config=ch_config,
            merchant_rate=ch.merchant_rate, cost_rate=ch.cost_rate,
        )
    else:
        resolved = await choose_channel(
            session, cache, order.payment_type_id, order.money, merchant_rate=merchant.rate
        )
    if not resolved:
        return PayFlowResult(ok=False, error="无可用支付通道")

    # 金额计算：实付金额（realmoney）按商户随机金额设置抖动，
    # 商户申报金额 money 保持不变，到账/成本/利润按实付计算。
    # 随机金额单 realmoney 只随机一次（首次拉起落库后保持不变）——
    # 幂等重提交/收银台换支付方式重复拉起若重新随机，会导致已分发的
    # 付款链接金额与订单不符、回调金额比对失败
    realmoney = _quant(order.money)
    if merchant.random_enable and merchant.random_min > 0 and merchant.random_max > 0:
        if order.realmoney is not None and order.realmoney > 0:
            realmoney = _quant(order.realmoney)
        else:
            rmin, rmax = merchant.random_min, merchant.random_max
            if rmin > rmax:
                rmin, rmax = rmax, rmin
            if rmin == rmax:
                realmoney = Decimal(int(rmin)).quantize(CENT)
            else:
                realmoney = Decimal(random.randint(int(rmin), int(rmax))).quantize(CENT)
    merchant_income, channel_cost, profit = compute_amounts(
        realmoney, resolved.merchant_rate, resolved.cost_rate
    )

    # 事务 A：落通道与金额（无外部 IO）
    order.channel_id = resolved.channel.id
    order.plugin_name = resolved.channel.plugin_name
    order.realmoney = realmoney
    order.merchant_income = merchant_income
    order.channel_cost = channel_cost
    order.profit = profit
    session.add(order)
    await session.commit()

    # 事务外：插件下单（HTTP IO）
    plugin = PluginRegistry.get_payment(resolved.channel.plugin_name, resolved.config)
    if not plugin:
        _dbg_submit(resolved.channel, order, "no_plugin", is_mobile=is_mobile)
        return PayFlowResult(ok=False, error=f"支付插件 {resolved.channel.plugin_name} 未安装")

    ctx = build_payment_context(
        order, resolved.channel.plugin_name, notify_base, return_base, site_url, is_mobile,
    )
    try:
        result: PaymentResult = await maybe_await(plugin.submit(ctx))
    except Exception as e:
        _log.exception("插件下单异常 plugin=%s trade_no=%s", resolved.channel.plugin_name, order.trade_no)
        _dbg_submit(resolved.channel, order, "exception", ctx, is_mobile,
                    error=traceback.format_exc(), plugin=plugin)
        return PayFlowResult(ok=False, error="上游通道请求失败，请稍后重试", plugin=plugin)

    if result.kind == "error" or (result.kind != "jump" and not result.url and not result.html):
        _dbg_submit(resolved.channel, order, "biz_error", ctx, is_mobile,
                    result={"kind": result.kind, "url": result.url,
                            "html": result.html, "msg": result.msg},
                    plugin=plugin)
        return PayFlowResult(ok=False, error=result.msg or "获取支付接口数据失败", plugin=plugin)

    # 事务 B：存支付信息
    order.extra = {**(order.extra or {}), "pay_type": result.kind, "pay_info": (result.url or result.html)[:2000]}
    session.add(order)
    await session.commit()

    _dbg_submit(resolved.channel, order, "result", ctx, is_mobile,
                result={"kind": result.kind, "url": result.url, "html": result.html,
                        "msg": result.msg, "expires_in": result.expires_in},
                plugin=plugin)
    return PayFlowResult(
        ok=True,
        pay_type=result.kind,
        pay_info=result.url or result.html,
        expires_in=result.expires_in,
        plugin=plugin,
    )


# ============================================================
# 扣量（按成功订单频次轮询抽单，默认关闭）
# ============================================================

async def _hit_deduct(session: AsyncSession, order: Order,
                      cache: CacheClient | None = None) -> bool:
    """扣量判定：deduct_enable 开启且 frequency>0 时，按成功订单计数轮询。

    金额≤deduct_minmoney（配置>0 时）豁免；命中即第 frequency 笔成功单。
    计数优先走 Redis INCR 原子计数 —— 无行锁，多进程/多实例并发下天然原子，
    模运算自然循环无需重置；Redis 不可用回退 counters 表行级 FOR UPDATE。
    """
    from app.services.settings import get_setting, setting_enabled

    settings_all = None
    try:
        from app.services.settings import get_all_settings
        settings_all = await get_all_settings(session)
    except Exception:
        pass
    if settings_all is not None:
        enabled = setting_enabled(settings_all.get("deduct_enable", False))
        try:
            frequency = int(settings_all.get("deduct_frequency", 0) or 0)
        except (TypeError, ValueError):
            frequency = 0
        min_money = dmoney(settings_all.get("deduct_minmoney", "0.00") or 0)
    else:
        enabled = bool(await get_setting(session, "deduct_enable", False))
        frequency = await get_int_setting(session, "deduct_frequency")
        from app.services.settings import get_money_setting
        min_money = await get_money_setting(session, "deduct_minmoney")

    if not enabled or frequency <= 0:
        return False
    if min_money > 0 and dmoney(order.money) <= min_money:
        return False

    # Redis 原子计数：INCR 单命令原子，取模命中即第 frequency 笔（模自然循环，无需重置）
    if cache is not None:
        try:
            n = await cache.incr(CacheKeys.DEDUCT_ORDER_COUNTER)
            return int(n) % frequency == 0
        except Exception:
            _log.warning("扣量 Redis 计数异常，回退行锁", exc_info=True)

    # 回退：counters 行 FOR UPDATE（无 Redis / 测试环境）
    counter = (await session.exec(
        select(Counter).where(Counter.key == "deduct_order").with_for_update()
    )).first()
    if not counter:
        counter = Counter(key="deduct_order", value=0)
        session.add(counter)
    counter.value += 1
    hit = counter.value % frequency == 0
    if hit:
        counter.value = 0  # 命中后清零，重新累计
    session.add(counter)
    return hit


async def undo_deduct(session: AsyncSession, order: Order) -> bool:
    """扣量补单（admin）：补入账 + 补邀请返现 + 排队补发通知。返回是否成功。"""
    if not order.is_deducted or order.status != ORDER_PAID:
        return False

    income = _quant(order.merchant_income) if order.merchant_income is not None else None
    if income is None:
        income = _quant(order.money)

    order.is_deducted = False
    if income > 0:
        await change_merchant_balance(
            session, order.merchant_id, income,
            biz_type="order_income", trade_no=order.trade_no, remark="扣量补单入账",
        )
        await _apply_invite_rebate(session, order.trade_no, order.merchant_id, income)

    order.notify_status = 1
    order.notify_count = 0
    order.next_notify_at = datetime.now()
    session.add(order)
    await session.commit()
    return True


# ============================================================
# 回调幂等记账（单一漏斗：上游回调 / 主动查单补单 / 手动补单共用）
# ============================================================

async def process_paid_order(
    trade_no: str,
    api_trade_no: str,
    paid_realmoney: Decimal | str | None,
    buyer_id: str,
    session: AsyncSession,
    cache: CacheClient | None = None,
) -> PaidResult:
    """幂等记账：状态机只允许 0→1；同事务更新订单 + 写流水 + 加余额。

    paid_realmoney 为上游声称的实付金额，与 order.realmoney 不符返回 mismatch。
    """
    lock_key = f"lock:order:{trade_no}"
    if cache is not None:
        got = await try_lock(cache, lock_key, ttl=30)
        if got is False:
            # 并发处理中：对上游表现为成功（对方会收到幂等结果）
            order = await get_order_by_trade_no(session, trade_no)
            return PaidResult(status="lock_failed", order=order)

    try:
        order = (await session.exec(
            select(Order).where(Order.trade_no == trade_no).with_for_update()
        )).first()
        if not order:
            return PaidResult(status="not_found")
        if order.status == ORDER_PAID:
            return PaidResult(status="duplicate", order=order)
        if order.status != ORDER_PENDING:
            return PaidResult(status="invalid", order=order)

        # 金额比对（services 统一做，插件只报数）
        if paid_realmoney is not None:
            try:
                claimed = dmoney(paid_realmoney)
            except Exception:
                return PaidResult(status="mismatch", order=order)
            expected = _quant(order.realmoney if order.realmoney is not None else order.money)
            if claimed != expected:
                _log.warning("金额不符 trade_no=%s claimed=%s expected=%s", trade_no, claimed, expected)
                return PaidResult(status="mismatch", order=order)

        # 扣量判定（仅普通收款单；命中：标记已支付但不入账/不返佣/不通知，
        # 商户侧一切视图显示为未支付）
        if order.order_type == 0 and await _hit_deduct(session, order, cache):
            order.is_deducted = True
            order.status = ORDER_PAID
            order.paid_at = datetime.now()
            if api_trade_no:
                order.api_trade_no = api_trade_no[:64]
            if buyer_id:
                order.buyer_id = buyer_id[:64]
            order.notify_status = 0  # 不进入通知队列
            await session.commit()
            return PaidResult(status="paid", order=order, should_notify=False)

        # 记账（充值单全额入账；普通单按落库金额，未算过则按费率兜底重算）
        if order.order_type == 1:
            income = _quant(order.money)
            order.merchant_income = income
            order.channel_cost = Decimal("0.00")
            order.profit = Decimal("0.00")
        else:
            income = _quant(order.merchant_income) if order.merchant_income is not None else None
            if income is None:
                # 兜底：按通道费率重算（正常流程 process_payment 已落库）
                from app.services.channel import get_channel_with_config
                channel, _cfg = await get_channel_with_config(session, order.channel_id)
                if channel is not None:
                    from app.models import Merchant as _M
                    m = await session.get(_M, order.merchant_id)
                    rate = m.rate if (m and m.rate is not None) else channel.merchant_rate
                    c_rate = channel.cost_rate
                else:
                    rate = Decimal("0")
                    c_rate = Decimal("0")
                income, cost, profit = compute_amounts(_quant(order.money), dmoney(rate), dmoney(c_rate))
                order.merchant_income, order.channel_cost, order.profit = income, cost, profit

        if order.order_type == 1:
            # 充值单：全额入账、不回调商户、不计邀请返现
            if income > 0:
                await change_merchant_balance(
                    session, order.merchant_id, income,
                    biz_type="recharge", trade_no=trade_no, remark="余额充值",
                )
                from app.models import Merchant as _M
                from app.services.message import send_merchant_message
                m = await session.get(_M, order.merchant_id)
                if m:
                    await send_merchant_message(
                        session, m, title="充值成功",
                        content=f"充值单 {trade_no} 已到账 {income} 元。",
                        biz_type="recharge",
                    )
        elif income > 0:
            await change_merchant_balance(
                session, order.merchant_id, income,
                biz_type="order_income", trade_no=trade_no, remark="在线收款",
            )
            await _apply_invite_rebate(session, trade_no, order.merchant_id, income)

        order.status = ORDER_PAID
        order.paid_at = datetime.now()
        if api_trade_no:
            order.api_trade_no = api_trade_no[:64]
        if buyer_id:
            order.buyer_id = buyer_id[:64]
        if order.order_type == 1:
            # 充值单无商户回调
            order.notify_status = 0
        else:
            # 首次通知标记：待重试，立即首次投递
            order.notify_status = 1
            order.notify_count = 0
            order.next_notify_at = datetime.now()

        await session.commit()
        should_notify = order.order_type != 1
        return PaidResult(status="paid", order=order, should_notify=should_notify)
    except Exception:
        await session.rollback()
        raise
    finally:
        if cache is not None:
            await release_lock(cache, lock_key)


async def _apply_invite_rebate(session: AsyncSession, trade_no: str, merchant_id: int, income: Decimal) -> None:
    """邀请返现：按订单到账金额 × invite_rebate_rate% 实时加到邀请人余额（0=关闭）。

    返现失败不阻断主记账（配置缺失/邀请人不存在时静默跳过）。
    """
    if income <= 0:
        return
    from app.services.settings import get_money_setting
    try:
        rate = await get_money_setting(session, "invite_rebate_rate")
    except Exception:
        return
    if rate <= 0:
        return
    rebate = (income * rate / Decimal(100)).quantize(CENT)
    if rebate <= 0:
        return
    try:
        from app.models import Merchant as _M
        m = await session.get(_M, merchant_id)
        if not m or not m.invited_by:
            return
        await change_merchant_balance(
            session, m.invited_by, rebate,
            biz_type="invite_rebate", trade_no=trade_no,
            remark=f"邀请返现（费率{rate}%）",
        )
    except Exception:
        _log.warning("邀请返现失败 trade_no=%s merchant_id=%s", trade_no, merchant_id, exc_info=True)


# ============================================================
# 平台 → 商户通知 / 回跳参数（RSA2 签名，平台私钥）
# ============================================================

def build_webhook_payload(order: Order, merchant: Merchant, platform_rsa_key: str = "") -> dict:
    """构建 Webhook JSON body：业务参数 + timestamp + sign（平台私钥 RSA2 签名）。

    商户用平台公钥对 body（去 sign/sign_type）做 get_sign_content 拼串验签。
    money 为申报金额固定两位小数（fmt_money）；param 空则省略键。
    """
    data = {
        "pid": str(merchant.pid),
        "trade_no": order.trade_no,
        "out_trade_no": order.out_trade_no or "",
        "type": order.payment_type_code or "",
        "name": order.subject or "",
        "money": fmt_money(order.money),
        "trade_status": "TRADE_SUCCESS",
        "timestamp": str(int(time.time())),
    }
    if order.param:
        data["param"] = order.param
    if platform_rsa_key:
        data["sign"] = make_rsa_sign(data, platform_rsa_key)
        data["sign_type"] = "RSA2"
    return data


def append_query(url: str, params: dict) -> str:
    """把签名参数拼到 URL（自动处理 ? / &）。"""
    from urllib.parse import urlencode
    if not url:
        return ""
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{urlencode(params)}"


def build_signed_return_url(order: Order, merchant: Merchant, platform_rsa_key: str = "") -> str:
    """同步回跳地址：return_url + 签名参数（平台私钥 RSA2 签名，商户用平台公钥验签）。"""
    if not order.return_url:
        return ""
    return append_query(order.return_url, build_webhook_payload(order, merchant, platform_rsa_key))


def refundable_base(order: Order) -> Decimal:
    """可退上限基数：merchant_income（NULL 兜底 money）。"""
    return dmoney(order.merchant_income if order.merchant_income is not None else order.money)


async def payment_type_name_map(session: AsyncSession) -> dict[str, str]:
    """支付方式 code → 展示名（payment_types 表口径，管理端可改名）。

    订单只存 payment_type_code（alipay/wxpay…），面板展示中文名统一走这里，
    避免前端硬编码映射与后台「支付方式」配置脱节。
    """
    rows = (await session.exec(select(PaymentType.code, PaymentType.name))).all()
    return {str(r[0]): str(r[1]) for r in rows}


async def order_query_payload(order: Order, session: AsyncSession | None = None) -> dict:
    """开放 API 查单响应 data（GET /orders/{trade_no}）。

    扣量单对外一律显示为未支付（无完成时间/无退款信息）。
    refund_status：NONE 未退 / PARTIAL 部分退 / FULL 全退（仅已支付订单有意义，
    传入 session 时按 refunds 实时聚合，否则按订单状态推导）。
    """
    deducted = bool(order.is_deducted)
    paid = order.status in (ORDER_PAID, ORDER_REFUNDED)
    refunded = Decimal("0.00")
    if not deducted and paid and session is not None:
        from app.services.refund import refunded_amount
        refunded = await refunded_amount(session, order.trade_no)
    if deducted or not paid or refunded <= 0:
        refund_status, refunded_str = "NONE", "0.00"
    elif order.status == ORDER_REFUNDED or refunded >= refundable_base(order):
        refund_status, refunded_str = "FULL", fmt_money(refunded)
    else:
        refund_status, refunded_str = "PARTIAL", fmt_money(refunded)
    return {
        "trade_no": order.trade_no,
        "out_trade_no": order.out_trade_no,
        "type": order.payment_type_code,
        "name": order.subject,
        "money": fmt_money(order.money),
        "status": 0 if deducted else (1 if paid else 0),
        "addtime": order.created_at.strftime("%Y-%m-%d %H:%M:%S") if order.created_at else "",
        "endtime": "" if deducted else (order.paid_at.strftime("%Y-%m-%d %H:%M:%S") if order.paid_at else ""),
        "refund_status": refund_status,
        "refunded_amount": refunded_str,
    }

