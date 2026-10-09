import logging
import logging.handlers
import queue
from contextvars import ContextVar

from app.core.config import config

# 贯穿单次请求的 request_id：由 HTTP 中间件设置，日志 Filter 注入每条记录，
# 便于把一次请求内的所有日志（含异常）关联起来排查。
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


def get_request_id() -> str:
    """返回当前请求的 request_id（无请求上下文时为 '-'）。"""
    return request_id_var.get()


class RequestIdFilter(logging.Filter):
    """把 contextvar 中的 request_id 注入每条日志记录，供格式化引用 %(request_id)s。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


# 用命名 logger，而非 root：避免捕获 uvicorn / sqlalchemy 等第三方日志、
# 与 uvicorn 自带 handler 重复输出，以及 APP_DEBUG 时第三方库 DEBUG 刷屏。
# 支付插件独立于 app 包（plugins/ 目录，logger 名为 plugins.* 或老插件惯用的
# plugin.*），一并纳入同一套 handler，避免重构后插件日志丢失。
_LOG_NAMESPACES = ("app", "plugins", "plugin")

for _name in _LOG_NAMESPACES:
    _ns = logging.getLogger(_name)
    _ns.setLevel(logging.DEBUG if config.APP_DEBUG else logging.INFO)
    # 命名 logger 默认不向 root 传播（root 无业务 handler），避免重复输出
    _ns.propagate = False

logger = logging.getLogger("app")

# 格式带上 request_id 与行号，定位更高效
formatter = logging.Formatter(
    "%(levelname)s %(asctime)s rid=%(request_id)s %(pathname)s:%(lineno)d\n%(message)s"
)

# 输出到文件中（按天轮转，保留30天）
from pathlib import Path
log_dir = Path("storage/logs")
log_dir.mkdir(parents=True, exist_ok=True)

to_file = logging.handlers.TimedRotatingFileHandler(
    filename=str(log_dir / "app.log"),
    when="midnight",
    interval=1,
    backupCount=30,
    encoding="utf-8",
)
to_file.setFormatter(formatter)
to_file.suffix = "%Y-%m-%d"

# 实际落盘/控制台的 handler 列表（由 QueueListener 在后台线程驱动）
_targets = [to_file]
# 控制台输出（容器环境推荐打开，配合 LOG_TO_CONSOLE=true）
if getattr(config, "LOG_TO_CONSOLE", False):
    to_console = logging.StreamHandler()
    to_console.setFormatter(formatter)
    _targets.append(to_console)

# 非阻塞日志：logger 只挂 QueueHandler。每条日志在「调用线程」内先经 RequestIdFilter
# （此刻 request_id ContextVar 仍是当前请求值），再把记录入队；格式化 + 文件 write/flush
# 由 QueueListener 的后台线程执行。
#
# 关键性能点：访问日志在请求中间件里对每个请求打 INFO，原先同步文件 write+flush
# 跑在事件循环线程上，高 QPS 下会串行化所有请求；改队列后 IO 不再阻塞事件循环。
# 注意 RequestIdFilter 必须挂在 QueueHandler 上、而非 target 上——Listener 线程
# 处于不同的 context，那时 request_id_var 已是默认值 '-'。
_log_queue: queue.Queue = queue.Queue(-1)
_queue_handler = logging.handlers.QueueHandler(_log_queue)
_queue_handler.addFilter(RequestIdFilter())
for _name in _LOG_NAMESPACES:
    logging.getLogger(_name).addHandler(_queue_handler)

_listener = logging.handlers.QueueListener(_log_queue, *_targets)
_listener.start()
