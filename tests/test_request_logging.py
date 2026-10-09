"""request_id 链路追踪与日志重构回归。

- 中间件：透传/生成 X-Request-Id 并在响应头回带，每个请求打一条访问日志
- RequestIdFilter：把 contextvar 中的 rid 注入日志记录
- 命名空间接线：app / plugins / plugin 顶层 logger 挂队列 handler、不向 root 传播，
  插件日志不因命名 logger 重构而丢失
"""
import logging
import logging.handlers

from app.core.logger import RequestIdFilter, logger, request_id_var


async def test_request_id_echoed_from_incoming_header(client):
    """外部传入 X-Request-Id 时原样透传并在响应头回带。"""
    r = await client.get("/health", headers={"X-Request-Id": "rid-echo-123"})
    assert r.status_code == 200
    assert r.headers["X-Request-Id"] == "rid-echo-123"


async def test_request_id_generated_when_missing(client):
    """未传 X-Request-Id 时自动生成（uuid4 前 16 位 hex）。"""
    r = await client.get("/health")
    assert r.status_code == 200
    rid = r.headers["X-Request-Id"]
    assert rid and rid != "-"
    assert len(rid) == 16 and all(c in "0123456789abcdef" for c in rid)


async def test_access_log_written_per_request(client):
    """请求中间件对每个请求打一条 method/path/status/duration 访问日志。"""
    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            captured.append(record)

    cap = _Capture()
    logger.addHandler(cap)
    try:
        await client.get("/health")
    finally:
        logger.removeHandler(cap)

    assert any(
        r.getMessage().startswith("req GET /health status=200") for r in captured
    ), [r.getMessage() for r in captured]


def test_request_id_filter_injects_contextvar():
    """Filter 从 contextvar 取 rid 注入记录；无上下文时为默认 '-'。"""
    record = logging.LogRecord(
        "app.test", logging.INFO, __file__, 1, "hello", None, None
    )
    token = request_id_var.set("rid-filter-42")
    try:
        assert RequestIdFilter().filter(record) is True
        assert record.request_id == "rid-filter-42"
    finally:
        request_id_var.reset(token)

    record2 = logging.LogRecord(
        "app.test", logging.INFO, __file__, 1, "hello", None, None
    )
    RequestIdFilter().filter(record2)
    assert record2.request_id == "-"


def test_log_namespaces_wired():
    """app/plugins/plugin 三个顶层 logger 均挂队列 handler 且不向 root 传播。

    plugins 目录独立于 app 包（含老插件惯用的 plugin.* 命名），漏挂会导致
    插件日志在命名 logger 重构后静默丢失（propagate=False 不再落到 root）。
    """
    for name in ("app", "plugins", "plugin"):
        lg = logging.getLogger(name)
        assert lg.propagate is False, name
        assert any(
            isinstance(h, logging.handlers.QueueHandler) for h in lg.handlers
        ), name
