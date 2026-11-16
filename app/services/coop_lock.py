"""进程内协作锁 —— DB 行锁的补充，不是替代。

生产（MySQL）的资金串行化靠 `SELECT ... FOR UPDATE`（见 services/balance.lock_merchant）：
行锁是跨进程/跨实例的唯一正确串行化点。但两件事它做不到：

1. SQLite 测试库不支持 `FOR UPDATE`（语法被忽略），并发回归用例在 CI 里只能靠
   进程内的协作锁复现「同一结算单并发打款 / 同一订单并发退款」的确定性语义；
2. 单进程内的热点争抢（同一结算单被连点、同一订单被并发退款）先在这里互斥，
   可以少一次无谓的 DB 锁等待。

锁按 key 引用计数，最后一个使用者释放后从表里删除，避免长跑进程累积 key。
只允许「无嵌套」使用（本模块内部），不跨模块嵌套以免引入锁序问题。
"""
import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

_locks: dict[str, tuple[asyncio.Lock, int]] = {}


@asynccontextmanager
async def coop_lock(key: str) -> AsyncIterator[None]:
    """按 key 互斥（进程内）。异常/取消也会正确释放引用计数。"""
    lock, refs = _locks.get(key, (asyncio.Lock(), 0))
    _locks[key] = (lock, refs + 1)
    try:
        async with lock:
            yield
    finally:
        entry = _locks.get(key)
        if entry is not None:
            lock_obj, used = entry
            if used <= 1:
                _locks.pop(key, None)
            else:
                _locks[key] = (lock_obj, used - 1)
