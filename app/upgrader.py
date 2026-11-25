"""面板一键升级执行器（compose profile ``upgrade`` 的 upgrader 服务）。

不带业务依赖：只用 httpx（同步客户端 + unix socket）直连 Docker API，
不经 docker CLI / compose 二进制 —— 镜像自身即可执行，客户无需额外拉取
docker:cli 等镜像。循环做三件事：

1. 心跳：往 ``storage/upgrade/heartbeat.json`` 写时间戳（app 侧据此判断
   一键升级是否可用）；
2. 孤儿请求清理：超过时限仍未处理的请求标记失败（upgrader 重启/失联过）；
3. 消费 ``storage/upgrade/request.json``（后台 POST /admin-api/upgrade/run
   写入）：

   拉镜像 → 同步宿主 .env 的 PAY_IMAGE → 一次性容器跑 alembic 迁移 →
   逐台重建 worker/app 容器（沿用原容器配置，等健康检查通过再动下一台，
   多副本近似无感）→ 用新镜像再造一个自身容器后退出（自更新，下次升级
   用的就是新版执行器；旧容器 exit 0，restart: on-failure 不会拉起）。

安全口径：本容器持有 /var/run/docker.sock（等价宿主 root），默认不随编排
启动，仅在客户确认后于 .env 加 COMPOSE_PROFILES=upgrade 开启（DEPLOY.md）。
"""
import fcntl
import json
import os
import socket
import sys
import time
import traceback

import httpx

from app.services.upgrade import (
    clear_request,
    read_request,
    read_result,
    read_upgrade_state,
    upgrade_dir,
    write_heartbeat,
)

DOCKER_SOCKET = os.environ.get("UPGRADER_DOCKER_SOCKET", "/var/run/docker.sock")
POLL_SECONDS = 5
REQUEST_STALE_SECONDS = 20 * 60  # 请求超时仍未被处理 → 判定中断
MIGRATE_TIMEOUT = 300
ENV_SYNC_TIMEOUT = 60
HEALTH_TIMEOUT = 180
STOP_GRACE = 15

LABEL_PROJECT = "com.docker.compose.project"
LABEL_SERVICE = "com.docker.compose.service"
# 换代容器（-next）与一次性容器打的自有标记：不挂 compose 标签（避免被
# compose 当成服务成员 / 撞名），靠它做单实例清理
MARKER_LABEL = "pay-gateway.upgrader"


class DockerApiError(Exception):
    pass


class DockerClient:
    """Docker Engine API 的最小封装（同步 httpx + unix domain socket）。"""

    def __init__(self, uds: str = DOCKER_SOCKET, timeout: float = 60):
        self._http = httpx.Client(
            transport=httpx.HTTPTransport(uds=uds), timeout=timeout)
        self._base = "http://docker"

    def _check(self, r: httpx.Response) -> httpx.Response:
        if r.status_code >= 400:
            raise DockerApiError(
                f"{r.request.method} {r.request.url.path} → HTTP {r.status_code}: {r.text[:300]}")
        return r

    def get(self, path: str, params: dict | None = None):
        return self._check(self._http.get(self._base + path, params=params)).json()

    def post(self, path: str, params: dict | None = None, body: dict | None = None):
        return self._check(
            self._http.post(self._base + path, params=params, json=body))

    def delete(self, path: str):
        return self._check(self._http.delete(self._base + path))

    # ---- 高层操作 ----

    def ping(self) -> None:
        self._check(self._http.get(self._base + "/_ping"))

    def pull(self, image: str) -> str:
        """拉取镜像（流式），返回最后一条进度描述；失败抛 DockerApiError。"""
        repo, _, tag = image.rpartition(":")
        if not repo:  # 形如 "pay-gateway"（无 tag）——按 API 语义补 latest
            repo, tag = image, "latest"
        last = ""
        with self._http.stream(
            "POST", self._base + "/images/create",
            params={"fromImage": repo, "tag": tag},
        ) as r:
            if r.status_code >= 400:
                raise DockerApiError(f"拉取 {image} 失败：HTTP {r.status_code} {r.read()[:300]}")
            for line in r.iter_lines():
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if obj.get("error"):
                    raise DockerApiError(f"拉取 {image} 失败：{obj['error']}")
                last = obj.get("status") or last
        return last

    def inspect_container(self, cid: str) -> dict:
        return self.get(f"/containers/{cid}/json")

    def list_containers(self, label: str, running_only: bool = True) -> list[dict]:
        return self.get("/containers/json", params={
            "all": "0" if running_only else "1",
            "filters": json.dumps({"label": [label]}),
        })

    def inspect_image(self, ref: str) -> dict:
        return self.get(f"/images/{ref}/json")

    def logs_tail(self, cid: str, tail: int = 30) -> str:
        r = self._check(self._http.get(
            self._base + f"/containers/{cid}/logs",
            params={"stdout": "1", "stderr": "1", "tail": str(tail), "timestamps": "0"},
        ))
        # 非 TTY 日志是 [8字节头(流类型+大端长度) + payload] 多路复用帧
        out, buf, i = [], r.content, 0
        while i + 8 <= len(buf):
            size = int.from_bytes(buf[i + 4:i + 8], "big")
            payload = buf[i + 8:i + 8 + size]
            out.append(payload.decode("utf-8", errors="replace"))
            i += 8 + size
        return "\n".join(out).strip()[-2000:]

    def wait_exit(self, cid: str, timeout: float) -> int:
        deadline = time.time() + timeout
        while time.time() < deadline:
            st = self.inspect_container(cid).get("State", {})
            if not st.get("Running") and st.get("Status") != "restarting":
                return int(st.get("ExitCode") or 0)
            time.sleep(2)
        raise DockerApiError(f"容器 {cid} 在 {timeout}s 内未退出")

    def wait_healthy(self, cid: str, timeout: float = HEALTH_TIMEOUT) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            info = self.inspect_container(cid)
            st = info.get("State", {})
            health = st.get("Health")
            if not health:
                if st.get("Running"):
                    time.sleep(3)  # 无健康检查：起来即认为就绪
                    return
            else:
                status = health.get("Status")
                if status == "healthy":
                    return
                if status == "unhealthy":
                    raise DockerApiError(f"容器 {info['Name']} 健康检查未通过")
            time.sleep(2)
        raise DockerApiError(f"等待容器健康超时（{timeout}s）")

    def create(self, name: str, body: dict) -> str:
        r = self.post("/containers/create", params={"name": name}, body=body)
        return r.json()["Id"]

    def start(self, cid: str) -> None:
        self.post(f"/containers/{cid}/start")

    def stop_and_rm(self, cid: str, grace: int = STOP_GRACE) -> None:
        try:
            self.post(f"/containers/{cid}/stop", params={"t": str(grace)})
        except DockerApiError:
            pass  # 已停止/已不存在
        try:
            self.delete(f"/containers/{cid}")
        except DockerApiError:
            pass


# ============================================================
# 纯函数：容器重建参数推导（tests 直接覆盖）
# ============================================================

def merge_env(old_env: list[str], new_image_env: list[str]) -> list[str]:
    """新镜像 ENV 为基底，旧容器环境里只保留新镜像没有的键。

    镜像默认值（PATH、APP_VERSION 等）随新镜像走 —— 否则升级后版本号
    仍报旧值；compose/env_file 注入的运维变量（DB_*、APP_SECRET_KEY 等）
    新镜像里没有，原样保留。
    """
    new_keys = {e.split("=", 1)[0] for e in new_image_env}
    return list(new_image_env) + [
        e for e in old_env if e.split("=", 1)[0] not in new_keys]


def _networking(old_ct: dict) -> dict:
    eps = {}
    for name, net in (old_ct.get("NetworkSettings", {}).get("Networks") or {}).items():
        eps[name] = {
            "Aliases": net.get("Aliases") or [],
            **({"IPAMConfig": net["IPAMConfig"]} if net.get("IPAMConfig") else {}),
        }
    return {"EndpointsConfig": eps}


def build_create_body(
    old_ct: dict, new_image: str, new_image_env: list[str],
    old_image_cfg: dict | None = None,
    cmd_override: list[str] | None = None, drop_ports: bool = False,
    standalone: bool = False,
) -> dict:
    """旧容器 inspect → 新容器 create body。

    - Cmd/Entrypoint：与旧镜像默认一致说明来自镜像（不携带，让新镜像默认
      生效）；不一致说明 compose 显式指定（如 worker 的 command），原样保留。
      cmd_override 优先级最高（一次性容器）。
    - Labels 原样保留（含 compose 的 config-hash 等）：compose 据此继续纳管
      重建后的容器，宿主下次 deploy.sh 时按 hash 差异平滑重建（.env 的
      PAY_IMAGE 已同步，重建仍指向新版本）。standalone=True 时（换代容器/
      一次性容器）剥掉全部 compose 标签并打上 upgrader 自有标记 —— 它们
      不属于 compose 服务成员。
    - HostConfig 整体沿用（端口/挂载/extra_hosts/重启策略）；
      drop_ports 供一次性容器避免抢占宿主端口。
    - Healthcheck 沿用容器值（compose 定义的 worker 健康检查）；
      为空则自然继承新镜像的 HEALTHCHECK。
    """
    cfg = old_ct.get("Config", {})
    host_config = dict(old_ct.get("HostConfig") or {})
    if drop_ports:
        host_config["PortBindings"] = {}

    labels = dict(cfg.get("Labels") or {})
    if standalone:
        # 保留 project/service 供单实例清理定位，剥掉其余 compose 标签
        # （尤其 config-hash：没有它 compose 不把容器当作服务成员，避免与
        # compose 重建的规范名容器撞名/撞哈希），再打自有标记
        labels = {
            k: v for k, v in labels.items()
            if k in (LABEL_PROJECT, LABEL_SERVICE)
        }
        labels[MARKER_LABEL] = "1"
    body: dict = {
        "Image": new_image,
        "Env": merge_env(cfg.get("Env") or [], new_image_env),
        "Labels": labels,
        "WorkingDir": cfg.get("WorkingDir") or "",
        "User": cfg.get("User") or "",
        "HostConfig": host_config,
        "NetworkingConfig": _networking(old_ct),
    }
    if cfg.get("Healthcheck"):
        body["Healthcheck"] = cfg["Healthcheck"]
    if cmd_override is not None:
        body["Cmd"] = cmd_override
    else:
        old_default = (old_image_cfg or {}).get("Cmd")
        if old_image_cfg is None or cfg.get("Cmd") != old_default:
            body["Cmd"] = cfg.get("Cmd")
        # 与旧镜像默认一致 → 不携带，继承新镜像 CMD
    old_ep = (old_image_cfg or {}).get("Entrypoint")
    if cfg.get("Entrypoint") and (old_image_cfg is None or cfg.get("Entrypoint") != old_ep):
        body["Entrypoint"] = cfg["Entrypoint"]
    return body


# 宿主 .env 的 PAY_IMAGE 回写脚本（一次性容器内执行；保留原属主/权限，
# os.replace 原子替换后，后续新建容器按路径重新挂载即可读到新值）
_ENV_SYNC_SCRIPT = r"""
import os, sys
path = "/deploy/.env"
image = sys.argv[1]
st = os.stat(path)
lines = open(path, encoding="utf-8").read().splitlines()
out, done = [], False
for ln in lines:
    if ln.startswith("PAY_IMAGE="):
        if not done:
            out.append("PAY_IMAGE=" + image)
            done = True
    else:
        out.append(ln)
if not done:
    out.append("PAY_IMAGE=" + image)
tmp = path + ".payup.tmp"
with open(tmp, "w", encoding="utf-8") as f:
    f.write("\n".join(out) + "\n")
os.chmod(tmp, st.st_mode & 0o777 or 0o600)
os.chown(tmp, st.st_uid, st.st_gid)
os.replace(tmp, path)
print("PAY_IMAGE ->", image)
"""


class UpgradeRunner:
    """一次升级请求的完整执行；进度实时写 result.json 供后台轮询。"""

    def __init__(self, dc: DockerClient, base_dir: str | None = None):
        self.dc = dc
        self.base = base_dir
        self.dir = upgrade_dir(base_dir)
        self._result: dict = {}
        self.replaced_self = False

    # ---- 进度落盘 ----

    def _flush(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "result.json").write_text(
            json.dumps(self._result, ensure_ascii=False), encoding="utf-8")

    def log(self, msg: str, state: str | None = None) -> None:
        step = {"t": int(time.time()), "msg": msg}
        self._result["steps"].append(step)
        if state:
            self._result["state"] = state
        self._flush()
        print(f"[upgrader] {msg}", flush=True)

    # ---- 入口 ----

    def run(self, req: dict) -> None:
        self._result = {
            "id": req["id"], "state": "running", "target": req["version"],
            "image": req["image"], "steps": [], "started_at": int(time.time()),
        }
        self._flush()
        try:
            self._execute(req)
            self.log("升级完成", state="success")
        except Exception as e:  # 任何一步失败：保留现场，未被波及的容器照常服务
            self.log(f"升级失败：{e}", state="failed")
            traceback.print_exc()
        finally:
            self._result["finished_at"] = int(time.time())
            self._flush()
            clear_request(self.base)

    # ---- 步骤 ----

    def _execute(self, req: dict) -> None:
        image = req["image"]

        self.log(f"拉取镜像 {image} ...")
        try:
            status = self.dc.pull(image)
            self.log(f"镜像就绪（{status or '已存在'}）")
        except DockerApiError as e:
            # 离线场景：镜像可能已 docker load 到本机（对应 deploy.sh --no-pull）
            try:
                self.dc.inspect_image(image)
            except DockerApiError:
                raise e
            self.log(f"拉取失败但本机已有该镜像，继续（{e}）")
        image_info = self.dc.inspect_image(image)
        new_image_id, new_env = image_info["Id"], image_info.get("Config", {}).get("Env") or []

        # 自身信息 → compose 项目名（所有同项目容器即本次升级范围）
        self_ct = self.dc.inspect_container(socket.gethostname())
        project = (self_ct.get("Config", {}).get("Labels") or {}).get(LABEL_PROJECT)
        if not project:
            raise DockerApiError("无法识别 compose 项目（upgrader 容器缺少项目标签）")
        targets = [
            c for c in self.dc.list_containers(f"{LABEL_PROJECT}={project}")
            if (c.get("Labels") or {}).get(LABEL_SERVICE) in ("app", "worker")
        ]
        # worker 先动（后台任务短暂中断无感），app 逐台等健康再动下一台
        svc_order = {"worker": 0, "app": 1}
        targets.sort(key=lambda c: (
            svc_order.get((c.get("Labels") or {}).get(LABEL_SERVICE), 9), c["Names"][0]))
        if not targets:
            raise DockerApiError("未发现 app/worker 容器，请先用 deploy.sh 完成部署")

        # 1) 同步宿主 .env 的 PAY_IMAGE（防后续手工 deploy.sh 把版本拉回旧标签）
        self._sync_env_file(targets, image)

        # 2) 新镜像一次性容器执行 alembic 迁移（失败即中止，不动线上容器）
        self._run_migrate(req, targets, image, new_env)

        # 3) 逐台重建
        for c in targets:
            self._roll(c, image, new_image_id, new_env)

        # 4) 自更新：新镜像再造一个 upgrader，自身 exit 0 退场
        self._replace_self(self_ct, image, new_image_id, new_env)

    def _oneoff(self, name: str, body: dict, timeout: float, what: str) -> None:
        """一次性容器：create → start → 等退出；非零退出码带日志尾巴报错。"""
        try:
            self.dc.delete(f"/containers/{name}")
        except DockerApiError:
            pass
        cid = self.dc.create(name, body)
        try:
            self.dc.start(cid)
            rc = self.dc.wait_exit(cid, timeout)
            if rc != 0:
                raise DockerApiError(f"{what}失败（退出码 {rc}）：\n{self.dc.logs_tail(cid)}")
        finally:
            try:
                self.dc.delete(f"/containers/{cid}")
            except DockerApiError:
                pass

    def _template(self, targets: list[dict]) -> dict:
        """取 app 容器（优先）或 worker 容器的 inspect，作为一次性容器模板。"""
        names = [c["Names"][0].lstrip("/") for c in targets]
        pick = next((n for n in names if "-app-" in n), names[0])
        return self.dc.inspect_container(pick)

    def _sync_env_file(self, targets: list[dict], image: str) -> None:
        tpl = self._template(targets)
        env_src = next((m.get("Source") for m in tpl.get("Mounts") or []
                        if m.get("Destination") == "/app/.env"), "")
        if not env_src:
            self.log("未发现 .env 挂载，跳过 PAY_IMAGE 回写（下次 deploy.sh 需手动确认）")
            return
        host_dir = os.path.dirname(env_src)
        self.log("同步宿主 .env 的 PAY_IMAGE ...")
        try:
            self._oneoff(
                "pay-upgrade-envsync", {
                    "Image": image, "Cmd": ["python", "-c", _ENV_SYNC_SCRIPT, image],
                    "Labels": {MARKER_LABEL: "envsync"},
                    "HostConfig": {"Binds": [f"{host_dir}:/deploy"], "NetworkMode": "none"},
                }, ENV_SYNC_TIMEOUT, "同步 .env")
        except DockerApiError as e:
            self.log(f"警告：{e}")  # 非致命：升级继续，仅提示

    def _run_migrate(self, req: dict, targets: list[dict], image: str, new_env: list) -> None:
        tpl = self._template(targets)
        tpl_image_cfg = self.dc.inspect_image(tpl["Image"]).get("Config")
        self.log("执行数据库增量迁移（新镜像一次性容器）...")
        self._oneoff(
            f"pay-upgrade-migrate-{req['id']}",
            build_create_body(tpl, image, new_env, tpl_image_cfg,
                              cmd_override=["python", "artisan.py", "migrate", "upgrade"],
                              drop_ports=True, standalone=True),
            MIGRATE_TIMEOUT, "数据库迁移")

    def _roll(self, c: dict, image: str, new_image_id: str, new_env: list[str]) -> None:
        cid, name = c["Id"], c["Names"][0].lstrip("/")
        old_ct = self.dc.inspect_container(cid)
        if old_ct.get("Image") == new_image_id:
            self.log(f"{name} 已是目标版本，跳过")
            return
        old_image_cfg = self.dc.inspect_image(old_ct["Image"]).get("Config")
        body = build_create_body(old_ct, image, new_env, old_image_cfg)
        self.log(f"重建 {name} ...")
        self.dc.post(f"/containers/{cid}/stop", params={"t": str(STOP_GRACE)})
        self.dc.delete(f"/containers/{cid}")
        new_id = self.dc.create(name, body)
        self.dc.start(new_id)
        self.dc.wait_healthy(new_id)
        self.log(f"{name} 已更新并健康")

    def _replace_self(self, self_ct: dict, image: str, new_image_id: str, new_env: list) -> None:
        if self_ct.get("Image") == new_image_id:
            return  # 已是新镜像（重跑同一请求/补执行），无需自更新
        name = self_ct["Name"].lstrip("/")
        next_name = f"{name}-next"
        try:
            existing = self.dc.inspect_container(next_name)
            running = existing.get("State", {}).get("Running")
            self.dc.stop_and_rm(existing["Id"])
            if running:
                self.log(f"警告：{next_name} 原本在运行，已将其移除后重建")
        except DockerApiError:
            pass
        old_image_cfg = self.dc.inspect_image(self_ct["Image"]).get("Config")
        # 换代容器是独立容器（剥 compose 标签 + 打标记）：compose 重建规范名
        # upgrader-1 时不会与它撞名/撞哈希，由单实例清理兜底收敛
        body = build_create_body(self_ct, image, new_env, old_image_cfg, standalone=True)
        cid = self.dc.create(next_name, body)
        self.dc.start(cid)
        self.replaced_self = True
        self.log("已启动新版升级执行器，旧执行器即将退出")


def _cleanup_stale_request(base: str | None) -> None:
    """超时未被处理的请求标记失败（upgrader 曾离线/重启打断过升级）。"""
    req = read_request(base)
    if not req:
        return
    res = read_result(base)
    if res and res.get("id") == req.get("id"):
        return  # 正在/已经处理
    if time.time() - int(req.get("requested_at") or 0) < REQUEST_STALE_SECONDS:
        return
    result = {
        "id": req["id"], "state": "failed", "target": req.get("version", ""),
        "image": req.get("image", ""),
        "steps": [{"t": int(time.time()),
                   "msg": "升级请求长时间未被处理（执行器离线过），已作废，请重试"}],
        "started_at": int(req.get("requested_at") or 0), "finished_at": int(time.time()),
    }
    (upgrade_dir(base) / "result.json").write_text(
        json.dumps(result, ensure_ascii=False), encoding="utf-8")
    clear_request(base)
    print(f"[upgrader] 已作废超时请求 {req.get('id')}", flush=True)


def _epoch(v) -> float:
    """容器 Created 字段 → 秒级时间戳（新旧 Engine 返回 int/ISO 串不一致）。"""
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        from datetime import datetime
        try:
            return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0


def _sweep_duplicates(dc: DockerClient, self_ct: dict, lock_path) -> None:
    """收敛 upgrader 到单实例：清理同项目 upgrader 容器 / 带标记的换代与
    一次性残留容器（compose 重建的规范名、-next 换代、崩溃遗留的
    pay-upgrade-* 一次性容器）。

    规则：只清理比自己「旧」的容器（创建时间全序，最新者即唯一存活者 ——
    compose 刚重建的规范名容器可能尚在 created/starting 状态，碰它等于抹掉
    新一代）；有升级正在进行（锁被占）时整个清理跳过，绝不打断执行中的升级。
    """
    try:
        me = self_ct["Id"]
        my_created = _epoch(self_ct.get("Created"))
        project = (self_ct.get("Config", {}).get("Labels") or {}).get(LABEL_PROJECT)

        # 锁被占用 = 有升级在执行，动容器有风险，本轮放弃清理
        probe = open(lock_path, "a+")
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return
        finally:
            probe.close()

        candidates: dict[str, dict] = {}
        pools = [dc.list_containers(f"{LABEL_PROJECT}={project}", running_only=False)]
        try:  # 换代/一次性容器无 compose 标签，按自有标记与命名约定兜底
            pools.append(dc.get("/containers/json", params={
                "all": "1", "filters": json.dumps({"label": [MARKER_LABEL]})}))
            pools.append(dc.get("/containers/json", params={
                "all": "1", "filters": json.dumps({"name": ["pay-upgrade-"]})}))
        except DockerApiError:
            pass
        for pool in pools:
            for c in pool or []:
                labels = c.get("Labels") or {}
                is_upgrader_service = labels.get(LABEL_SERVICE) == "upgrader" \
                    and labels.get(LABEL_PROJECT) == project
                if c["Id"] == me:
                    continue
                if is_upgrader_service or MARKER_LABEL in labels \
                        or c["Names"][0].lstrip("/").startswith("pay-upgrade-"):
                    candidates[c["Id"]] = c
        for cid, c in candidates.items():
            if _epoch(c.get("Created")) >= my_created:
                continue  # 不比我旧：可能是 compose 刚重建/启动中的新一代，由它清理我
            dc.stop_and_rm(cid, grace=5)
            print(f"[upgrader] 清理重复/残留容器 {c['Names'][0].lstrip('/')}", flush=True)
    except Exception:
        traceback.print_exc()  # 清理失败不影响主循环


def main() -> None:
    base = os.environ.get("UPGRADER_STORAGE") or None
    uds = os.environ.get("UPGRADER_DOCKER_SOCKET", DOCKER_SOCKET)
    dc = DockerClient(uds)
    try:
        dc.ping()
    except Exception as e:
        print(f"[upgrader] 无法连接 Docker（{uds}）：{e}", file=sys.stderr, flush=True)
        sys.exit(1)
    d = upgrade_dir(base)
    d.mkdir(parents=True, exist_ok=True)
    print(f"[upgrader] 启动：存储 {d}，socket {uds}", flush=True)

    # 升级期间独占（自更新换代/部署方扩副本时可能有短暂双活）
    lock_path = d / ".lock"
    lock_fd = open(lock_path, "a+")
    self_ct: dict | None = None

    while True:
        replaced = False
        try:
            if self_ct is None:
                self_ct = dc.inspect_container(socket.gethostname())
            write_heartbeat(base)
            _sweep_duplicates(dc, self_ct, lock_path)
            _cleanup_stale_request(base)
            if read_upgrade_state(base)["state"] == "queued":
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    pass  # 另一个执行器正在升级，本轮跳过
                else:
                    try:
                        req = read_request(base)
                        if req:
                            runner = UpgradeRunner(dc, base)
                            runner.run(req)
                            replaced = runner.replaced_self
                    finally:
                        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except Exception:
            traceback.print_exc()
        if replaced:
            print("[upgrader] 自更新完成，退出", flush=True)
            sys.exit(0)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
