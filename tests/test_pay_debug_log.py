"""支付通道调试日志测试。

- write_pay_debug 基础行为：文件名 /tmp/pay_{日期}_{插件}.log、单行格式、字段截断
- 容错：目标目录不可写时静默不抛
- 开关关闭时三个挂载点（下单/回调/查单）不产生任何文件
- 开关开启时下单产生 SUBMIT 条目，且不泄漏通道凭证
"""
import hashlib
from datetime import datetime

import pytest

import app.services.pay_debug as pay_debug
from app.services.pay_debug import write_pay_debug
from tests.conftest import signed_params


@pytest.fixture
def dbg_dir(tmp_path, monkeypatch):
    """调试日志目录重定向到 tmp_path（PAY_DEBUG_DIR 同款覆盖）。"""
    monkeypatch.setattr(pay_debug, "_DIR", str(tmp_path))
    return tmp_path


def _sign_epay(data: dict, key: str) -> str:
    items = sorted(
        (k, v) for k, v in data.items()
        if k not in ("sign", "sign_type") and v is not None and v != ""
        and not isinstance(v, (list, dict))
    )
    raw = "&".join(f"{k}={v}" for k, v in items) + key
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


# ============================================================
# write_pay_debug 基础行为
# ============================================================

def test_write_pay_debug_filename_and_content(dbg_dir):
    write_pay_debug("epay", "SUBMIT", {"trade_no": "T123456", "stage": "result"})

    fname = f"pay_{datetime.now():%Y%m%d}_epay.log"
    path = dbg_dir / fname
    assert path.exists(), f"未生成 {fname}"
    content = path.read_text(encoding="utf-8")
    assert "T123456" in content
    # 单行格式：时间 | 事件名固定对齐 | 单行 JSON
    assert "| SUBMIT |" in content
    assert "trade_no" in content


def test_write_pay_debug_bytes_decoded(dbg_dir):
    write_pay_debug("epay", "NOTIFY", {"body": b"\xff\xfe<xml>ok</xml>"})
    path = dbg_dir / f"pay_{datetime.now():%Y%m%d}_epay.log"
    content = path.read_text(encoding="utf-8")
    assert "<xml>ok</xml>" in content  # bytes decode(errors=replace)


def test_write_pay_debug_field_truncated(dbg_dir):
    write_pay_debug("epay", "QUERY", {"raw": "x" * 5000, "trade_no": "T1"})
    path = dbg_dir / f"pay_{datetime.now():%Y%m%d}_epay.log"
    content = path.read_text(encoding="utf-8")
    assert "...(truncated)" in content
    # 单字段截 2000，单行 32KB
    assert len(content) < 32 * 1024
    assert content.count("\n") == 1


def test_write_pay_debug_unwritable_silent(monkeypatch, tmp_path):
    """目标目录不存在（不可写）时静默吞掉，绝不抛异常。"""
    monkeypatch.setattr(pay_debug, "_DIR", str(tmp_path / "no" / "such" / "dir"))
    write_pay_debug("epay", "SUBMIT", {"trade_no": "T1"})  # 不抛即通过


# ============================================================
# 三挂载点：开关关闭零文件 / 开启产出且不泄漏凭证
# ============================================================

@pytest.mark.asyncio
async def test_switch_off_no_files(client, seeded, engine, dbg_dir):
    """通道 debug_log=0（默认）：下单 + 回调 + 查单全流程零日志文件。"""
    params = {
        "pid": "1001", "type": "alipay", "out_trade_no": "DBG1",
        "notify_url": "https://merchant.example/notify", "name": "测试", "money": "100.00",
    }
    params = signed_params(params)
    resp = await client.post("/orders", json=params)
    assert resp.json()["code"] == 0
    trade_no = resp.json()["data"]["trade_no"]

    # 上游回调（验签不通过的乱参数也会走 _load_plugin，但关闭时不写）
    await client.post(f"/callbacks/epay/{trade_no}?foo=bar")

    # 主动查单
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from sqlmodel.ext.asyncio.session import AsyncSession
    import app.worker.jobs as jobs_mod
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    real = jobs_mod.session_maker
    jobs_mod.session_maker = maker
    try:
        await jobs_mod.query_upstream_job(None, trade_no)
    finally:
        jobs_mod.session_maker = real

    assert list(dbg_dir.iterdir()) == [], "开关关闭不应产生调试日志文件"


@pytest.mark.asyncio
async def test_switch_on_submit_logged_no_credentials(client, seeded, engine, dbg_dir):
    """通道 debug_log=1：下单写 SUBMIT 条目，且不泄漏通道凭证（appkey）。"""
    from app.models import Channel
    from app.core.database import get_db
    from main import app as _app

    override = _app.dependency_overrides[get_db]
    async for s in override():
        ch = await s.get(Channel, seeded["channel"].id)
        ch.debug_log = 1
        s.add(ch)
        await s.commit()

    params = {
        "pid": "1001", "type": "alipay", "out_trade_no": "DBG2",
        "notify_url": "https://merchant.example/notify", "name": "测试", "money": "100.00",
    }
    params = signed_params(params)
    resp = await client.post("/orders", json=params)
    assert resp.json()["code"] == 0
    trade_no = resp.json()["data"]["trade_no"]

    # 回调（乱签名 → verdict_reject）与主动查单（QUERY result）
    await client.post(f"/callbacks/epay/{trade_no}?foo=bar")
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from sqlmodel.ext.asyncio.session import AsyncSession
    import app.worker.jobs as jobs_mod
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    real = jobs_mod.session_maker
    jobs_mod.session_maker = maker
    try:
        await jobs_mod.query_upstream_job(None, trade_no)
    finally:
        jobs_mod.session_maker = real

    path = dbg_dir / f"pay_{datetime.now():%Y%m%d}_epay.log"
    assert path.exists()
    content = path.read_text(encoding="utf-8")
    assert trade_no in content
    assert "| SUBMIT |" in content
    assert '"stage": "result"' in content or '"stage":"result"' in content
    assert "| NOTIFY |" in content
    assert "verdict_reject" in content
    assert "| QUERY |" in content
    # 密钥红线：上游 appkey 绝不出现在日志
    from tests.conftest import UPSTREAM_KEY
    assert UPSTREAM_KEY not in content
