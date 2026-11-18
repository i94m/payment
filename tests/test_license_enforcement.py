"""授权门禁的执行面：/health 授权状态、防时钟回拨、后台任务暂停。

密码学校验在 tests/test_license.py；这里覆盖「锁不锁得住」：
- /health 带 licensed 字段（软锁期间仍 200，便于监控告警）
- 过期判断取 max(系统时间, 见过的最晚时间)，回拨时钟不能续命
- worker 任务与 cron、以及 enqueue 在未授权时全部丢弃
"""
import time

import pytest

from app.core import license as lic


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    """清掉授权态与时钟缓存，避免用例间串扰（源码运行不读盘）。"""
    monkeypatch.setattr(lic, "current", None)
    monkeypatch.setattr(lic, "startup_error", None)
    monkeypatch.setattr(lic, "_max_seen", 0.0)
    monkeypatch.setattr(lic, "_last_persist", 0.0)
    monkeypatch.setattr(lic, "_clock_loaded", True)


# ============================================================
# /health 暴露授权状态
# ============================================================

async def test_health_exposes_license_state(client, monkeypatch):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["licensed"] is True

    # 过期：/health 仍 200 但 licensed=false（供监控告警），业务接口 503
    monkeypatch.setattr(lic, "current", {"exp": int(time.time()) - 60})
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["licensed"] is False
    assert (await client.get("/admin-api/pay/channels")).status_code == 503


async def test_health_licensed_false_on_startup_error(client, monkeypatch):
    monkeypatch.setattr(lic, "startup_error", "Invalid license code: License code is empty")
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["licensed"] is False


# ============================================================
# 防时钟回拨
# ============================================================

def test_effective_now_never_goes_backwards(monkeypatch):
    future = time.time() + 3600
    monkeypatch.setattr(lic, "_max_seen", future)

    # 系统时间被回拨一天：仍按见过的最晚时间计
    monkeypatch.setattr(lic.time, "time", lambda: future - 86400)
    assert lic.effective_now() >= future


def test_clock_rollback_cannot_extend_license(monkeypatch):
    """曾见过「授权到期之后」的时间 → 即便回拨到到期前也判过期。"""
    now = int(time.time())
    monkeypatch.setattr(lic, "_max_seen", now + 3600)
    monkeypatch.setattr(lic, "current", {"exp": now + 60})

    reason = lic.lock_reason()
    assert reason and "expired" in reason
    assert lic.licensed() is False


def test_clock_persisted_and_restored_in_compiled_mode(tmp_path, monkeypatch):
    """编译镜像把「见过的最晚时间」落盘，进程重启后仍能识别回拨。"""
    clock = tmp_path / "license_clock"
    monkeypatch.setenv(lic.CLOCK_FILE_ENV, str(clock))
    monkeypatch.setattr(lic, "_is_compiled", lambda: True)
    monkeypatch.setattr(lic, "_clock_loaded", False)

    seen = time.time() + 1000
    monkeypatch.setattr(lic.time, "time", lambda: seen)
    lic.effective_now()
    assert clock.exists()

    # 模拟进程重启（内存清空）+ 时钟回拨：从文件恢复出最晚时间
    monkeypatch.setattr(lic, "_clock_loaded", False)
    monkeypatch.setattr(lic, "_max_seen", 0.0)
    monkeypatch.setattr(lic.time, "time", lambda: seen - 5000)
    assert lic.effective_now() >= seen


def test_effective_now_updates_forward(monkeypatch):
    assert lic.effective_now() > 0
    # 正常前进：返回系统时间并记住
    future = time.time() + 10
    monkeypatch.setattr(lic.time, "time", lambda: future)
    assert lic.effective_now() == pytest.approx(future, abs=1)
    assert lic._max_seen >= future


# ============================================================
# 后台任务 / 入队在未授权时暂停
# ============================================================

async def test_worker_job_skipped_when_unlicensed(monkeypatch):
    import app.worker as worker

    calls: list[int] = []

    async def job(ctx, **kwargs):
        calls.append(1)

    guarded = worker.license_guard(job)

    monkeypatch.setattr(worker, "lock_reason", lambda: "License expired at ...")
    assert await guarded({}) is None
    assert calls == []          # 未授权：任务不执行

    monkeypatch.setattr(worker, "lock_reason", lambda: None)
    await guarded({})
    assert calls == [1]         # 恢复授权：照常执行


async def test_cron_jobs_are_guarded():
    import app.worker as worker

    assert worker.functions and worker.cron_jobs
    # 每个任务函数都被包了一层（functools.wraps 保留原名）
    for fn in worker.functions:
        assert getattr(fn, "__wrapped__", None) is not None, fn
    # cron 走同一批已加门禁的函数
    guarded_ids = {id(fn) for fn in worker.functions}
    for job in worker.cron_jobs:
        assert id(job.function) in guarded_ids, job.cron


async def test_enqueue_refused_when_unlicensed(monkeypatch):
    import app.worker as worker

    class _Queue:
        async def enqueue(self, *args, **kwargs):  # pragma: no cover - 不应被调用
            raise AssertionError("未授权时不应入队")

    monkeypatch.setattr(worker, "queue", _Queue())
    monkeypatch.setattr(worker, "lock_reason", lambda: "License expired at ...")
    assert await worker.enqueue("notify_merchant_job", trade_no="x") == ""
