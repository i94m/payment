"""版本升级：版本发现/标记协议/后台接口/容器重建参数推导。"""
import json
import time

import httpx
import pytest

from app.core.config import config
from app.services import upgrade as svc


# ============================================================
# 纯工具：版本排序 / 镜像引用解析
# ============================================================

def test_sort_versions_filters_and_sorts():
    tags = ["latest", "sha-abc1234", "1", "1.2", "1.0.0", "1.10.0", "1.2.3", "1.9.0"]
    assert svc.sort_versions(tags) == ["1.10.0", "1.9.0", "1.2.3", "1.0.0"]


def test_is_newer():
    assert svc.is_newer("1.0.1", "1.0.0")
    assert not svc.is_newer("1.0.0", "1.0.0")
    assert not svc.is_newer("1.0.0", "1.0.1")
    assert not svc.is_newer("1.2.3", "dev")  # 非语义化不算可升级
    assert not svc.is_newer("dev", "1.0.0")


def test_split_image_ref():
    assert svc.split_image_ref("ghcr.io/i94m/payment:1.2.3") == ("ghcr.io", "i94m/payment", "1.2.3")
    assert svc.split_image_ref("ghcr.io/i94m/payment") == ("ghcr.io", "i94m/payment", "latest")
    # registry 带端口不应被误认成标签
    assert svc.split_image_ref("registry.example.com:8443/ns/pay:v2") == (
        "registry.example.com:8443", "ns/pay", "v2")
    assert svc.split_image_ref("pay-gateway:dev") == ("", "pay-gateway", "dev")


def test_image_with_tag(monkeypatch):
    monkeypatch.setattr(config, "PAY_IMAGE", "ghcr.io/i94m/payment:1.0.0")
    assert svc.image_with_tag("1.2.3") == "ghcr.io/i94m/payment:1.2.3"
    monkeypatch.setattr(config, "PAY_IMAGE", "pay-gateway:latest")
    with pytest.raises(svc.UpgradeError):
        svc.image_with_tag("1.2.3")  # 本地镜像无仓库前缀


# ============================================================
# 版本发现：ghcr 匿名 token → tags/list（MockTransport）
# ============================================================

def _mock_registry(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_fetch_versions_ghcr_flow(monkeypatch):
    monkeypatch.setattr(config, "PAY_IMAGE", "ghcr.io/i94m/payment:1.0.0")
    monkeypatch.setattr(svc, "_version_cache", {})
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            assert request.url.params["scope"] == "repository:i94m/payment:pull"
            return httpx.Response(200, json={"token": "anon-token"})
        if request.url.path == "/v2/i94m/payment/tags/list":
            # 未带 token 的首次直连返回 401，验证 token 换取后重试
            if not request.headers.get("Authorization"):
                return httpx.Response(401, json={"errors": []})
            calls["n"] += 1  # 只计成功（带 token）的列表请求，用于验证缓存
            return httpx.Response(200, json={"tags": ["latest", "1.0.0", "1.0.1", "sha-x"]})
        return httpx.Response(404)

    versions = await svc.fetch_available_versions(client=_mock_registry(handler))
    assert versions == ["1.0.1", "1.0.0"]

    # 命中缓存：第二次不再发请求
    again = await svc.fetch_available_versions(client=_mock_registry(handler))
    assert again == versions and calls["n"] == 1


@pytest.mark.asyncio
async def test_fetch_versions_requires_semver(monkeypatch):
    monkeypatch.setattr(config, "PAY_IMAGE", "ghcr.io/i94m/payment:1.0.0")
    monkeypatch.setattr(svc, "_version_cache", {})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"tags": ["latest", "main", "sha-x"]})

    with pytest.raises(svc.UpgradeError, match="语义化版本"):
        await svc.fetch_available_versions(client=_mock_registry(handler))


# ============================================================
# 标记协议：请求/结果/心跳（面板与执行器之间的跨进程契约）
# ============================================================

def test_marker_protocol_states(tmp_path):
    assert svc.read_upgrade_state(tmp_path)["state"] == "idle"
    assert not svc.upgrader_alive(tmp_path)

    req = svc.write_request("1.0.1", "ghcr.io/i94m/payment:1.0.1", tmp_path)
    state = svc.read_upgrade_state(tmp_path)
    assert state["state"] == "queued" and state["version"] == "1.0.1"

    # 结果 id 不匹配（上一轮遗留）仍是 queued
    (tmp_path / "upgrade" / "result.json").write_text(
        json.dumps({"id": "old", "state": "success"}), encoding="utf-8")
    assert svc.read_upgrade_state(tmp_path)["state"] == "queued"

    # 匹配后进入终态
    (tmp_path / "upgrade" / "result.json").write_text(
        json.dumps({"id": req["id"], "state": "running", "steps": [
            {"t": 1, "msg": "拉取镜像"}]}), encoding="utf-8")
    state = svc.read_upgrade_state(tmp_path)
    assert state["state"] == "running" and state["steps"][0]["msg"] == "拉取镜像"

    svc.clear_request(tmp_path)
    # 请求清掉后保留最近结果（面板展示最近一次）
    assert svc.read_upgrade_state(tmp_path)["state"] == "running"


def test_heartbeat(tmp_path):
    svc.write_heartbeat(tmp_path)
    assert svc.upgrader_alive(tmp_path)
    (tmp_path / "upgrade" / "heartbeat.json").write_text(
        json.dumps({"ts": int(time.time()) - 9999}), encoding="utf-8")
    assert not svc.upgrader_alive(tmp_path)


# ============================================================
# 后台接口：检查 / 触发 / 状态（httpx 与文件目录均打桩）
# ============================================================

async def _admin_headers(session):
    from sqlmodel import select
    from app.core.security import hash_password, issue_token
    from app.models import SystemAdmin

    session.add(SystemAdmin(name="管理员", username="upgradeadmin",
                            password=hash_password("admin123"), is_super_admin=True))
    await session.commit()
    admin = (await session.exec(
        select(SystemAdmin).where(SystemAdmin.username == "upgradeadmin"))).first()
    token = await issue_token(guard="admin", user_id=admin.id, session=session)
    return {"Authorization": f"Bearer {token.access_token}"}


@pytest.mark.asyncio
async def test_upgrade_endpoints(session, client, monkeypatch, tmp_path):
    from app.routers.admin import upgrade as router_mod

    monkeypatch.setattr(config, "STORAGE_PATH", str(tmp_path))
    monkeypatch.setattr(config, "APP_VERSION", "1.0.0")
    monkeypatch.setattr(config, "PAY_IMAGE", "ghcr.io/i94m/payment:1.0.0")
    monkeypatch.setattr(svc, "_version_cache", {})

    async def fake_fetch(client=None, ttl=300):
        return ["1.0.2", "1.0.1", "1.0.0"]

    monkeypatch.setattr(router_mod, "fetch_available_versions", fake_fetch)
    headers = await _admin_headers(session)

    # 检查：当前 1.0.0 / 最新 1.0.2 / 一键升级未启用
    resp = await client.get("/admin-api/upgrade/check", headers=headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["current"] == "1.0.0" and data["latest"] == "1.0.2"
    assert data["has_update"] is True and data["upgrader"] is False

    # 未启用 upgrader 时拒绝触发，并给出命令行指引
    resp = await client.post("/admin-api/upgrade/run", headers=headers, json={"version": ""})
    assert "未启用" in resp.json()["message"]

    # upgrader 心跳就绪后触发：默认最新版
    svc.write_heartbeat(str(tmp_path))
    resp = await client.post("/admin-api/upgrade/run", headers=headers, json={"version": ""})
    assert resp.status_code == 200, resp.json()
    data = resp.json()["data"]
    assert data["version"] == "1.0.2"
    assert data["image"] == "ghcr.io/i94m/payment:1.0.2"
    assert svc.read_request(str(tmp_path))["version"] == "1.0.2"

    # 状态：queued（执行器尚未接手）→ 允许覆盖提交新请求
    resp = await client.get("/admin-api/upgrade/status", headers=headers)
    assert resp.json()["data"]["state"] == "queued"
    resp = await client.post("/admin-api/upgrade/run", headers=headers, json={"version": "1.0.1"})
    assert resp.status_code == 200 and resp.json()["data"]["version"] == "1.0.1"

    # 执行器已接手（result 与 request 同 id）→ 拒绝并发
    req = svc.read_request(str(tmp_path))
    (tmp_path / "upgrade" / "result.json").write_text(
        json.dumps({"id": req["id"], "state": "running", "steps": []}), encoding="utf-8")
    resp = await client.post("/admin-api/upgrade/run", headers=headers, json={"version": "1.0.1"})
    assert "已有升级任务" in resp.json()["message"]
    svc.clear_request(str(tmp_path))
    (tmp_path / "upgrade" / "result.json").unlink()

    # 不在可用列表的版本被拒绝
    resp = await client.post("/admin-api/upgrade/run", headers=headers, json={"version": "0.0.1"})
    assert "不在可用列表" in resp.json()["message"]

    # 非超管/无 system.admin 权限不可见
    resp = await client.get("/admin-api/upgrade/check")
    assert resp.status_code == 401


# ============================================================
# 容器重建参数推导（app/upgrader.py 纯函数）
# ============================================================

def _ct_fixture():
    """app 容器 inspect 的最小子集（compose 创建后的形态）。"""
    return {
        "Name": "/pay-app-1",
        "Id": "aaa111",
        "Image": "sha256:old",
        "Config": {
            "Image": "ghcr.io/i94m/payment:1.0.0",
            "Env": [
                "PATH=/app/.venv/bin:/usr/local/bin:/usr/bin:/bin",
                "APP_VERSION=1.0.0",
                "APP_SECRET_KEY=secret",
                "DB_HOST=host.docker.internal",
                "TZ=Asia/Shanghai",
            ],
            "Cmd": ["python", "-m", "saq", "app.worker.settings"],
            "Entrypoint": None,
            "Labels": {
                "com.docker.compose.project": "pay",
                "com.docker.compose.service": "worker",
                "com.docker.compose.config-hash": "expired",
                "com.docker.compose.container-number": "3",
            },
            "Healthcheck": {"Test": ["CMD", "true"]},
            "WorkingDir": "/app",
            "User": "",
        },
        "HostConfig": {
            "Binds": ["/srv/pay/storage:/app/storage"],
            "PortBindings": {"8000/tcp": [{"HostPort": "8000"}]},
            "ExtraHosts": ["host.docker.internal:host-gateway"],
            "NetworkMode": "pay_default",
        },
        "NetworkSettings": {"Networks": {
            "pay_default": {"Aliases": ["worker"], "IPAMConfig": None},
        }},
    }


def test_merge_env_image_defaults_refreshed():
    from app.upgrader import merge_env
    old_env = ["PATH=/old", "APP_VERSION=1.0.0", "DB_HOST=h", "APP_SECRET_KEY=s"]
    new_image_env = ["PATH=/new", "APP_VERSION=1.0.2"]
    merged = merge_env(old_env, new_image_env)
    assert merged == [
        "PATH=/new", "APP_VERSION=1.0.2", "DB_HOST=h", "APP_SECRET_KEY=s"]


def test_build_create_body_rolls_container():
    from app.upgrader import build_create_body

    ct = _ct_fixture()
    old_image_cfg = {
        # 旧镜像默认 CMD 是 uvicorn（Dockerfile CMD），与容器的 saq command 不同
        # → 说明 compose 显式指定，重建时必须沿用
        "Cmd": ["/bin/sh", "-c", "uvicorn main:app --host 0.0.0.0 --port 8000"],
        "Entrypoint": None,
    }
    body = build_create_body(
        ct, "ghcr.io/i94m/payment:1.0.2",
        ["PATH=/new", "APP_VERSION=1.0.2"], old_image_cfg)

    assert body["Image"] == "ghcr.io/i94m/payment:1.0.2"
    # compose 标签原样保留（含 config-hash）：compose 继续纳管，宿主下次
    # deploy.sh 按 hash 差异平滑重建而不是撞名报错
    assert body["Labels"]["com.docker.compose.service"] == "worker"
    assert body["Labels"]["com.docker.compose.config-hash"] == "expired"
    # 镜像默认值随新镜像，运维注入保留
    assert "PATH=/new" in body["Env"] and "APP_VERSION=1.0.2" in body["Env"]
    assert "DB_HOST=host.docker.internal" in body["Env"]
    # compose 显式 command 沿用；健康检查/挂载/端口沿用
    assert body["Cmd"] == ["python", "-m", "saq", "app.worker.settings"]
    assert body["Healthcheck"] == {"Test": ["CMD", "true"]}
    assert body["HostConfig"]["PortBindings"]["8000/tcp"]
    assert body["NetworkingConfig"]["EndpointsConfig"]["pay_default"]["Aliases"] == ["worker"]

    # 镜像默认 Cmd（与旧镜像一致）不携带 → 继承新镜像 CMD
    ct2 = _ct_fixture()
    ct2["Config"] = {**ct2["Config"], "Cmd": ["a", "default"]}
    body2 = build_create_body(ct2, "x:1", [], {"Cmd": ["a", "default"], "Entrypoint": None})
    assert "Cmd" not in body2

    # 一次性容器：覆盖 Cmd 且不占宿主端口
    body3 = build_create_body(
        ct, "ghcr.io/i94m/payment:1.0.2", [], old_image_cfg,
        cmd_override=["python", "artisan.py", "migrate", "upgrade"],
        drop_ports=True, standalone=True)
    assert body3["Cmd"] == ["python", "artisan.py", "migrate", "upgrade"]
    assert body3["HostConfig"]["PortBindings"] == {}
    # standalone：仅留 project/service 定位标签 + 自有标记，compose 不纳管
    assert body3["Labels"] == {
        "com.docker.compose.project": "pay",
        "com.docker.compose.service": "worker",
        "pay-gateway.upgrader": "1",
    }
