"""P3-30：商户通知走共享 httpx 客户端（不再每次投递新建 = 不再每次重新握手）。

覆盖点：
1. `get_notify_client()` 进程内复用同一个实例，关闭后能重新建立；
2. `close_http_client()` 把通知池也一起关掉（应用/worker 退出路径）；
3. `notify_merchant_once` 的实际投递确实走这个共享客户端（不是自建 client）。
"""
import pytest
from sqlmodel import select

from app.models import NotifyLog
from tests.test_fund_lifecycle import _pay_order


@pytest.mark.asyncio
async def test_shared_notify_client_is_reused_and_closed():
    """同一个实例被复用；close 后 is_closed 且下次取到新实例。"""
    from app.services import http_client as hc

    await hc.close_http_client()

    first = hc.get_notify_client()
    assert hc.get_notify_client() is first
    assert first.follow_redirects is False

    await hc.close_http_client()
    assert first.is_closed

    second = hc.get_notify_client()
    assert second is not first and not second.is_closed

    await hc.close_http_client()


@pytest.mark.asyncio
async def test_notify_delivery_uses_shared_client(client, seeded, session, monkeypatch):
    """投递走共享客户端：替换掉 get_notify_client 就能截到这次 POST。"""
    from app.services import notify as notify_mod

    order = await _pay_order(client, session, "SC-1")
    assert order.notify_status == 1  # 记账后排队待通知

    calls: list[dict] = []

    class _Resp:
        status_code = 200
        text = "OK"

    class _RecordingClient:
        async def post(self, url, content=None, headers=None):  # noqa: D401 - 假客户端
            calls.append({"url": url, "content": content, "headers": headers})
            return _Resp()

    async def _allow(_url: str) -> None:
        return None  # 跳过真实 DNS/私网校验，本用例只验「走的是哪个客户端」

    monkeypatch.setattr(notify_mod, "get_notify_client", lambda: _RecordingClient())
    monkeypatch.setattr(notify_mod, "assert_public_http_url", _allow)

    assert await notify_mod.notify_merchant_once(session, trade_no=order.trade_no) is True

    assert len(calls) == 1
    assert calls[0]["url"] == "https://merchant.example/notify"
    assert calls[0]["headers"]["Content-Type"].startswith("application/json")
    assert b"trade_no" in calls[0]["content"]

    log = (await session.exec(
        select(NotifyLog).where(NotifyLog.trade_no == order.trade_no)
        .execution_options(populate_existing=True))).first()
    assert log is not None and log.success is True and log.http_status == 200
