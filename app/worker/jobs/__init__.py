"""SAQ 任务定义（全部异步）。

事件任务：
- notify_merchant_job(trade_no)  支付成功后投递，发一次商户通知（阶梯重试逻辑在服务内）
- query_upstream_job(trade_no)   主动查上游补单，paid 走同一个幂等记账入口

Cron 任务：
- scan_pending_notify  每分钟：捡起到期的待重试通知 → enqueue
- scan_unpaid_orders   每分钟：近 N 分钟（默认 10）窗口内待支付订单 → enqueue 查单补单
- expire_orders        每 5 分钟：过期待支付订单分批置 status=3
- queue_monitor_task   每 2 分钟：队列/通知重试耗尽聚合巡检与告警

scans 用 Redis 锁防多 worker 重复扫描；Redis 不可用时跳过本轮（保守）。
"""
import logging
import traceback
from datetime import datetime, timedelta

from saq.types import Context
from saq.job import CronJob
from sqlalchemy import and_, update
from sqlmodel import select

from app.core.cache import try_lock
from app.core.database import session_maker
from app.core.logger import logger
from app.models import Order
from app.services.pay_debug import write_pay_debug

# 订单状态
PENDING, PAID, EXPIRED = 0, 1, 3

# 主动查单去重窗口（秒）：同一订单在该窗口内只投递一次查单任务
QUERY_DEDUPE_TTL = 120
# 过期任务分批：每批行数 / 单轮最多批数（避免停机恢复后长事务）
EXPIRE_BATCH = 1000
EXPIRE_MAX_BATCHES = 50


async def notify_merchant_job(ctx: Context, trade_no: str) -> None:
    """发一次商户通知（成功置 0；失败按阶梯设 next_notify_at，超限置 -1）。"""
    from app.services.notify import notify_merchant_once

    async with session_maker() as session:
        ok = await notify_merchant_once(session, trade_no=trade_no)
    if not ok:
        logger.info(f"notify_merchant_job: trade_no={trade_no} 本次投递失败，已按阶梯安排重试")


async def scan_pending_notify(ctx: Context) -> None:
    """每分钟：扫 notify_status>0 且 next_notify_at<=now 的订单，投递通知任务。"""
    from app.core.cache import get_cache_ctx

    async with get_cache_ctx() as cache:
        got = await try_lock(cache, "lock:scan_pending_notify", ttl=50)
        if got is False:
            return

    now = datetime.now()
    async with session_maker() as session:
        # 含已退款/已过期单：让 notify_merchant_once 的早退分支把遗留的
        # notify_status>0 收口为 0，否则这些行永远不会再被任何任务触达
        rows = (await session.exec(
            select(Order.trade_no)
            .where(Order.status != PENDING, Order.notify_status > 0)
            .where(Order.next_notify_at.isnot(None), Order.next_notify_at <= now)
            .limit(50)
        )).all()

    from app.worker import queue
    for trade_no in rows:
        await queue.enqueue("notify_merchant_job", trade_no=trade_no)
    if rows:
        logger.info(f"scan_pending_notify: 投递 {len(rows)} 笔待重试通知")


async def query_upstream_job(ctx: Context, trade_no: str) -> None:
    """主动查单补单：插件 query → paid 则走幂等记账 → 投递商户通知。"""
    from app.services.channel import get_channel_with_config
    from app.services.order import (
        ORDER_EXPIRED,
        ORDER_PENDING,
        get_order_by_trade_no,
        process_paid_order,
    )
    from app.services.plugin import PluginRegistry
    from app.services.plugin_interfaces import PaymentContext

    async with session_maker() as session:
        order = await get_order_by_trade_no(session, trade_no)
        # 待支付单补单；已过期单仍允许查一次（P0-4：过期后窗口内查到已付款要走回补）
        if not order or order.status not in (ORDER_PENDING, ORDER_EXPIRED):
            return
        channel, config = await get_channel_with_config(session, order.channel_id)
        if not channel:
            return
        dbg = bool(getattr(channel, "debug_log", 0))
        plugin = PluginRegistry.get_payment(channel.plugin_name, config)
        if not plugin:
            if dbg:
                write_pay_debug(channel.plugin_name, "QUERY", {
                    "trade_no": trade_no, "channel_id": channel.id, "stage": "no_plugin",
                })
            return

        ctx_data = PaymentContext(
            order={
                "trade_no": order.trade_no,
                "out_trade_no": order.out_trade_no,
                "api_trade_no": order.api_trade_no or "",
                "realmoney": str(order.realmoney or order.money),
                "payment_type_code": order.payment_type_code,
            },
            notify_url="", return_url="",
        )
        try:
            result = await plugin.query(ctx_data)
        except Exception as e:
            logger.warning(f"query_upstream_job: 查单异常 trade_no={trade_no}")
            if dbg:
                write_pay_debug(channel.plugin_name, "QUERY", {
                    "trade_no": trade_no, "channel_id": channel.id, "stage": "exception",
                    "error": f"{type(e).__name__}: {e}",
                    "traceback": traceback.format_exc(),
                    "ctx": {"order": ctx_data.order},
                    "upstream": getattr(plugin, "last_debug", None),
                })
            return

        if dbg:
            write_pay_debug(channel.plugin_name, "QUERY", {
                "trade_no": trade_no, "channel_id": channel.id, "stage": "result",
                "status": result.status,
                "api_trade_no": result.api_trade_no,
                "money": str(result.money) if result.money is not None else None,
                "buyer_id": result.buyer_id,
                "raw": result.raw,
            })

        if result.status == "paid":
            paid = await process_paid_order(
                trade_no, result.api_trade_no, result.money, result.buyer_id,
                session, None,
            )
            if dbg:
                write_pay_debug(channel.plugin_name, "QUERY", {
                    "trade_no": trade_no, "channel_id": channel.id, "stage": "paid_settled",
                    "paid_status": paid.status,
                    "should_notify": paid.should_notify,
                })
            if paid.status == "paid":
                from app.services.notify import notify_merchant_once
                await notify_merchant_once(session, trade_no=trade_no)
                logger.info(f"query_upstream_job: 补单成功 trade_no={trade_no}")
        elif result.status == "failed":
            logger.info(f"query_upstream_job: 上游确认订单失败 trade_no={trade_no}")


async def scan_unpaid_orders(ctx: Context) -> None:
    """每分钟：把**仍在有效期内**的待支付订单投递主动查单（补单）。

    口径（P0-4）：按 `expire_at` 扫描，而不是「近 N 分钟创建的」。
    原口径窗口（10 分钟）短于订单有效期（默认 30 分钟），订单有效期的后半段
    永远不会被查单 —— 一旦回调丢失，只能等 cron 把它置过期，钱卡死。
    现在扫描「有效期刚结束、或仍在有效期内」的待支付单（过期后仍保留
    query_unpaid_window 分钟的宽限，覆盖 cron 置过期前后的窗口）。

    去重（P3-28）：同一订单在 query 去重窗口内只投递一次，避免每分钟对同一批
    待支付单重复入队；按 expire_at 升序取最近要过期的单（高峰期优先处理最紧急的）。
    """
    from app.core.cache import get_cache_ctx
    from app.services.settings import get_all_settings, setting_enabled

    async with get_cache_ctx() as cache:
        got = await try_lock(cache, "lock:scan_unpaid", ttl=100)
        if got is False:
            return

        async with session_maker() as session:
            settings = await get_all_settings(session)
            if not setting_enabled(settings.get("query_unpaid_enable", True)):
                return
            try:
                window_minutes = int(settings.get("query_unpaid_window", "10"))
            except ValueError:
                window_minutes = 10
            try:
                expire_minutes = int(settings.get("order_expire_minutes", "30"))
            except ValueError:
                expire_minutes = 30

            now = datetime.now()
            grace = timedelta(minutes=max(window_minutes, 0))
            # expire_at 缺失的历史数据按 created_at + 最长有效期兜底
            longest = timedelta(minutes=max(expire_minutes, 0) + max(window_minutes, 0))
            rows = (await session.exec(
                select(Order.trade_no)
                .where(Order.status == PENDING, Order.channel_id > 0)
                .where(
                    (Order.expire_at >= now - grace)
                    | and_(Order.expire_at.is_(None), Order.created_at >= now - longest)
                )
                .order_by(Order.expire_at)
                .limit(100)
            )).all()

        from app.worker import queue
        sent = 0
        for trade_no in rows:
            # 去重窗口内同一订单只投递一次；Redis 抖动时宁可多投一次（查单本身幂等）
            try:
                fresh = await cache.set(f"scan:query:{trade_no}", "1", nx=True,
                                        ex=QUERY_DEDUPE_TTL)
            except Exception:
                fresh = True
            if not fresh:
                continue
            await queue.enqueue("query_upstream_job", trade_no=trade_no)
            sent += 1
        if sent:
            logger.info(f"scan_unpaid_orders: 投递 {sent} 笔主动查单")


async def expire_orders_task(ctx: Context) -> None:
    """每 5 分钟：过期待支付订单分批置 status=3（已过期）。

    分批（P3-29）：原来一条无 LIMIT 的 UPDATE，停机恢复后可能长时间锁住大批行；
    改为按 id 分批（每批 EXPIRE_BATCH 条），批内再用 status=PENDING 兜一次状态，
    防止「select 到 update 之间回调把它置已支付」被误改成过期。
    """
    now = datetime.now()
    total = 0
    async with session_maker() as session:
        for _ in range(EXPIRE_MAX_BATCHES):
            ids = (await session.exec(
                select(Order.id)
                .where(Order.status == PENDING,
                       Order.expire_at.isnot(None),
                       Order.expire_at < now)
                .limit(EXPIRE_BATCH)
            )).all()
            if not ids:
                break
            result = await session.exec(
                update(Order)
                .where(Order.id.in_(ids), Order.status == PENDING)
                .values(status=EXPIRED)
                .execution_options(synchronize_session=False)
            )
            await session.commit()
            total += result.rowcount or 0
            if len(ids) < EXPIRE_BATCH:
                break
    if total > 0:
        logger.info(f"expire_orders_task: 过期订单 {total} 笔")


# ============================================================
# 队列监控（可观测性：弥补 SAQ 无 flower 类监控的短板）
# ============================================================

# 增量基线 TTL：监控停开超过该时长后基线失效，重开首轮只重建基线不告警
_ALERT_BASELINE_TTL = 7 * 24 * 3600


async def _read_baseline(cache, key: str) -> int | None:
    """读上一轮基线；Redis 不可用/首次无基线返回 None（本轮不告警，仅建基线）。"""
    try:
        raw = await cache.get(key)
        return int(raw) if raw is not None else None
    except Exception:
        return None


async def _write_baseline(cache, key: str, value: int) -> None:
    try:
        await cache.set(key, value, ex=_ALERT_BASELINE_TTL)
    except Exception:
        pass


async def queue_monitor_task(ctx: Context) -> None:
    """每 2 分钟：SAQ 队列与商户通知重试耗尽的聚合巡检告警。

    - 通知重试耗尽（notify_status=-1）24h 窗口出现新增 → 告警
    - SAQ 任务失败 / 僵死任务被 sweep 回收 出现增量 → 告警
      （worker 重启计数清零只下调基线，不告警）
    - 队列积压（queued+scheduled）超 alert_queue_backlog → 告警
    - 每轮打一条 INFO 巡检日志（worker 数/队列计数），供日志平台采集

    alert_queue_enable 关闭时整段跳过；Redis 不可用时基线不可比，只保留日志巡检。
    """
    from app.core.cache import get_cache_ctx
    from app.services.settings import get_all_settings, setting_enabled

    from sqlmodel import col, func

    async with get_cache_ctx() as cache:
        got = await try_lock(cache, "lock:queue_monitor", ttl=100)
        if got is False:
            return

    async with session_maker() as session:
        settings = await get_all_settings(session)
        if not setting_enabled(settings.get("alert_queue_enable", True)):
            return

        alerts: list[tuple[str, str]] = []

        # --- 1) 商户通知重试耗尽（24h 窗口，与 notify_fail_block 联动口径一致）---
        since = datetime.now() - timedelta(hours=24)
        exhausted = int((await session.exec(
            select(func.count()).select_from(Order).where(
                col(Order.notify_status) == -1,  # noqa: E712
                col(Order.paid_at).isnot(None),
                col(Order.paid_at) >= since,
            )
        )).one())
        last_exhausted = await _read_baseline(cache, "monitor:notify_exhausted")
        if last_exhausted is not None and exhausted > last_exhausted:
            alerts.append((
                "商户通知重试耗尽",
                f"近 24 小时通知重试耗尽订单 {exhausted} 笔（较上轮新增 {exhausted - last_exhausted} 笔），"
                f"请检查商户回调地址，可在后台订单列表重发通知。",
            ))
        await _write_baseline(cache, "monitor:notify_exhausted", exhausted)

        # --- 2) SAQ 队列健康（worker 累计计数的跨轮增量）---
        from app.worker import queue as saq_queue
        try:
            info = await saq_queue.info()
        except Exception:
            logger.warning("queue_monitor_task: 读取 SAQ 队列信息失败", exc_info=True)
            info = None

        if info is not None:
            workers = info.get("workers") or {}
            queued = int(info.get("queued") or 0)
            active = int(info.get("active") or 0)
            scheduled = int(info.get("scheduled") or 0)
            total_failed = sum(int((w.get("stats") or {}).get("failed") or 0) for w in workers.values())
            total_aborted = sum(int((w.get("stats") or {}).get("aborted") or 0) for w in workers.values())

            last_failed = await _read_baseline(cache, "monitor:saq_failed")
            if last_failed is not None and total_failed > last_failed:
                alerts.append((
                    "队列任务失败",
                    f"SAQ 任务失败计数较上轮增加 {total_failed - last_failed}（当前累计 {total_failed}），"
                    f"请检查 worker 日志定位失败任务。",
                ))
            await _write_baseline(cache, "monitor:saq_failed", total_failed)

            last_aborted = await _read_baseline(cache, "monitor:saq_aborted")
            if last_aborted is not None and total_aborted > last_aborted:
                alerts.append((
                    "僵死任务回收",
                    f"检测到 {total_aborted - last_aborted} 个僵死任务被 sweep 回收"
                    f"（当前累计 {total_aborted}），可能有 worker 异常退出或任务超时。",
                ))
            await _write_baseline(cache, "monitor:saq_aborted", total_aborted)

            try:
                backlog_limit = int(settings.get("alert_queue_backlog", 500))
            except (TypeError, ValueError):
                backlog_limit = 500
            if backlog_limit > 0 and queued + scheduled > backlog_limit:
                alerts.append((
                    "队列积压",
                    f"队列积压 queued={queued} scheduled={scheduled}，超过阈值 {backlog_limit}，"
                    f"商户通知/主动查单可能延迟。",
                ))

            logger.info(
                f"queue_monitor_task: workers={len(workers)} queued={queued} active={active} "
                f"scheduled={scheduled} failed={total_failed} aborted={total_aborted} "
                f"notify_exhausted_24h={exhausted}"
            )

        from app.services.alert import send_admin_alert
        for title, content in alerts:
            await send_admin_alert(session, title, content)


# ============================================================
# 注册表
# ============================================================


async def merchant_cron_task(ctx: Context) -> None:
    """每 10 分钟商户侧批量任务：

    - 自动结算：auto_settle_enable 开启时，对 auto_settle=1 且可提余额达标的商户
      批量发起结算
    """
    from app.core.cache import get_cache_ctx
    from app.services.settings import get_all_settings, setting_enabled

    async with get_cache_ctx() as cache:
        got = await try_lock(cache, "lock:merchant_cron", ttl=540)
        if got is False:
            return

    async with session_maker() as session:
        settings = await get_all_settings(session)
        if setting_enabled(settings.get("auto_settle_enable", False)):
            from app.services.settle import auto_settle_merchants
            try:
                count = await auto_settle_merchants(session)
                if count:
                    logger.info(f"merchant_cron_task: 自动结算发起 {count} 笔")
            except Exception:
                logger.warning("merchant_cron_task: 自动结算异常", exc_info=True)


functions = [
    notify_merchant_job,
    scan_pending_notify,
    query_upstream_job,
    scan_unpaid_orders,
    expire_orders_task,
    queue_monitor_task,
    merchant_cron_task,
]

cron_jobs: list[CronJob] = [
    CronJob(scan_pending_notify, cron="* * * * *", timeout=60),
    CronJob(scan_unpaid_orders, cron="* * * * *", timeout=110),
    CronJob(expire_orders_task, cron="*/5 * * * *", timeout=120),
    CronJob(queue_monitor_task, cron="*/2 * * * *", timeout=60),
    CronJob(merchant_cron_task, cron="*/10 * * * *", timeout=300),
]
