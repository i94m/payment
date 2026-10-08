"""支付通道调试日志（通道级开关，服务层汇聚写入）。

用途：排查通道对接问题（下单参数错误 / 回调验签失败 / 掉单补单），
记录下单(SUBMIT)、上游回调(NOTIFY)、主动查单(QUERY)三个环节的
订单摘要与上游交互原文。

纪律（plugins/README.md §4）：
- 日志实现全部在服务层，插件绝不写 /tmp
- 不记录通道凭证（config），只记订单摘要 / 上游交互 / 结果
- 写日志失败静默吞掉，绝不影响支付主流程

文件：/tmp/pay_{当天日期}_{插件名}.log，按天自然切文件。
单行格式：2026-10-05 14:23:45.123 | SUBMIT | {单行 JSON}
grep trade_no 可串起一笔订单全链路。

并发：进程内 threading.Lock + open("a")（O_APPEND，POSIX 保证多进程
append 原子；uvicorn 多 worker 与 SAQ worker 共写安全）。
"""
import json
import os
import threading
from datetime import datetime

# 目录可被 PAY_DEBUG_DIR 覆盖（测试用）
_DIR = os.environ.get("PAY_DEBUG_DIR") or "/tmp"

# 三级保险丝：单字段 2000 / 单行 32KB / 单文件 64MB（超限停写）
_FIELD_MAX = 2000
_LINE_MAX = 32 * 1024
_FILE_MAX = 64 * 1024 * 1024

_lock = threading.Lock()


def _clip(v, limit: int = _FIELD_MAX):
    """单字段截断：bytes 先 decode(errors=replace)，超长截断。"""
    if isinstance(v, (bytes, bytearray)):
        v = bytes(v).decode("utf-8", errors="replace")
    if not isinstance(v, str):
        return v
    return v if len(v) <= limit else v[:limit] + "...(truncated)"


def _dumps(fields: dict) -> str:
    """dict → 单行 JSON；递归对 str/bytes 字段截断；失败回退 repr。"""
    try:
        def _default(o):
            return _clip(repr(o))

        return json.dumps(
            {k: _clip(v) for k, v in fields.items()},
            ensure_ascii=False, default=_default,
        )
    except Exception:
        return repr(fields)


def write_pay_debug(plugin_name: str, event: str, fields: dict) -> None:
    """写一条通道调试日志（全容错，失败静默）。

    plugin_name: 通道插件名（决定日志文件名）
    event:       事件名（SUBMIT/NOTIFY/QUERY，固定 5 字符对齐）
    fields:      业务字段（trade_no/stage/...）；调用方负责不带入凭证
    """
    try:
        now = datetime.now()
        path = os.path.join(
            _DIR, f"pay_{now:%Y%m%d}_{plugin_name}.log"
        )
        line = f"{now:%Y-%m-%d %H:%M:%S.%f} | {event:<5} | {_dumps(fields)}\n"
        if len(line) > _LINE_MAX:
            line = line[:_LINE_MAX] + "...(truncated)\n"
        with _lock:
            try:
                if os.path.exists(path) and os.path.getsize(path) > _FILE_MAX:
                    return  # 保险丝：单文件超 64MB 停写
            except OSError:
                return
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        pass
