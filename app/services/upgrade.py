"""版本升级 — 镜像仓库版本发现 + 面板升级的跨进程标记协议。

两条升级路径共用这里的版本发现：
- 服务器命令行：``bash scripts/deploy.sh upgrade``（经 artisan ``upgrade`` 子命令取版本列表）
- 管理后台一键升级：``/admin-api/upgrade/*`` 检查/触发/看进度

版本发现走 OCI Registry v2 匿名协议（ghcr.io 公开镜像无需登录）：
token → ``/v2/<repo>/tags/list`` → 过滤三段式语义化版本 → 降序。

面板升级不在 web 进程里碰 docker（app 容器没有 docker.sock），而是往
``storage/upgrade/``（compose 挂载的持久卷，全服务共享）写请求文件，
由可选的 upgrader sidecar（compose profile ``upgrade``，见 app/upgrader.py）
轮询执行，进度/结果同样以文件回传。
"""
import json
import re
import time
from pathlib import Path

import httpx

from app.core.config import config

# CI 只发三段式版本标签（v1.2.3 → 1.2.3）；1 / 1.2 / latest / sha-xxx 为浮动或定位标签
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
HEARTBEAT_MAX_AGE = 60  # upgrader 心跳超过该秒数视为未启用/已失联


class UpgradeError(Exception):
    """版本发现失败的统一异常（消息直接面向运维/后台展示）。"""


# ============================================================
# 纯工具：版本排序 / 镜像引用解析
# ============================================================

def parse_version(version: str) -> tuple[int, int, int] | None:
    """'1.2.3' → (1,2,3)；非三段式返回 None（比较时排在最前）。"""
    if not SEMVER_RE.match(version or ""):
        return None
    return tuple(int(x) for x in version.split("."))  # type: ignore[return-value]


def sort_versions(tags: list[str]) -> list[str]:
    """过滤出语义化版本并降序（最新在前）；非语义化标签（latest/sha-xxx/1/1.2）丢弃。"""
    pairs = [(parse_version(t), t) for t in tags]
    valid = [(p, t) for p, t in pairs if p is not None]
    return [t for _, t in sorted(valid, key=lambda x: x[0], reverse=True)]


def is_newer(candidate: str, current: str) -> bool:
    """candidate 是否高于 current；任一非语义化（如 dev）一律不算可升级。"""
    a, b = parse_version(candidate), parse_version(current)
    if a is None or b is None:
        return False
    return a > b


def split_image_ref(ref: str) -> tuple[str, str, str]:
    """镜像引用 → (registry, repo, tag)。

    'ghcr.io/i94m/payment:1.2.3' → ('ghcr.io', 'i94m/payment', '1.2.3')
    'pay-gateway:dev'            → ('', 'pay-gateway', 'dev')  # 本地镜像，无仓库
    首段含 '.'/':' 或为 localhost 视为 registry（OCI 惯例）。
    """
    ref = (ref or "").strip()
    tag = "latest"
    if ":" in ref.rsplit("/", 1)[-1]:  # 仅最后一段的 ':' 是标签（registry 带端口不受影响）
        ref, _, tag = ref.rpartition(":")
    if "/" in ref:
        first, _, rest = ref.partition("/")
        if "." in first or ":" in first or first == "localhost":
            return first, rest, tag
    return "", ref, tag


def image_with_tag(version: str) -> str:
    """当前镜像仓库 + 指定版本 → 完整镜像引用（后台触发升级用）。"""
    registry, repo, _ = split_image_ref(config.PAY_IMAGE or "")
    if not registry or not repo:
        # 无仓库前缀 = 本地构建镜像（pay-gateway:xx），面板无法在线拉取，
        # 该场景走服务器命令行（deploy.sh 可离线 --no-pull）
        raise UpgradeError("当前 PAY_IMAGE 不含镜像仓库前缀，无法在线升级")
    return f"{registry}/{repo}:{version}"


# ============================================================
# 版本发现（OCI Registry v2，匿名；ghcr 公开镜像无需凭据）
# ============================================================

# 进程内缓存：多副本各自缓存，避免每次点「检查更新」都打镜像仓库
_version_cache: dict[str, tuple[float, list[str]]] = {}


async def _list_tags(client: httpx.AsyncClient, registry: str, repo: str) -> list[str]:
    """按 registry 规范匿名列举标签：先直连，401 再走 token 换取后重试。"""
    if not registry:
        # 无 registry 前缀 = Docker Hub 或本地镜像：Hub 需要专用 token 服务，
        # 本项目镜像统一发布在 ghcr，不做 Hub 支持，直接给出可读错误
        raise UpgradeError("PAY_IMAGE 未带镜像仓库前缀（ghcr.io 等），无法在线查询版本")
    base = f"https://{registry}"
    tags_url = f"{base}/v2/{repo}/tags/list"

    resp = await client.get(tags_url)
    if resp.status_code == 401:
        # token 端点：ghcr 固定 /token；其他 registry（harbor 等）同为 /token，参数一致
        token_url = f"{base}/token"
        params = {"scope": f"repository:{repo}:pull"}
        if registry == "ghcr.io":
            params["service"] = "ghcr.io"
        tresp = await client.get(token_url, params=params)
        if tresp.status_code != 200:
            raise UpgradeError(f"获取 {registry} 匿名 token 失败（HTTP {tresp.status_code}）")
        token = (tresp.json().get("token") or tresp.json().get("access_token") or "").strip()
        if not token:
            raise UpgradeError(f"{registry} 未返回匿名 token（镜像可能未公开）")
        resp = await client.get(tags_url, headers={"Authorization": f"Bearer {token}"})
    if resp.status_code != 200:
        raise UpgradeError(
            f"拉取 {registry}/{repo} 标签列表失败（HTTP {resp.status_code}）："
            f"{resp.text[:200]}")
    tags = resp.json().get("tags") or []
    return tags if isinstance(tags, list) else []


async def fetch_available_versions(
    client: httpx.AsyncClient | None = None, ttl: float = 300,
) -> list[str]:
    """镜像仓库里可用的语义化版本，降序（最新在前）。

    结果按仓库缓存 ttl 秒；httpx 客户端可注入（测试用 MockTransport）。
    失败抛 UpgradeError（网络/私有镜像/无版本），调用方转成提示不炸接口。
    """
    registry, repo, _ = split_image_ref(config.PAY_IMAGE)
    if not repo:
        raise UpgradeError("PAY_IMAGE 未配置，无法查询可用版本（源码运行无镜像概念）")
    cache_key = f"{registry}/{repo}"
    cached = _version_cache.get(cache_key)
    now = time.time()
    if cached and now - cached[0] < ttl:
        return cached[1]

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=10)
    try:
        versions = sort_versions(await _list_tags(client, registry, repo))
    except httpx.HTTPError as e:
        raise UpgradeError(f"访问镜像仓库 {registry} 失败：{e}") from e
    finally:
        if own_client:
            await client.aclose()
    if not versions:
        raise UpgradeError(f"{cache_key} 下没有语义化版本标签（需以 vX.Y.Z 发版）")
    _version_cache[cache_key] = (now, versions)
    return versions


# ============================================================
# 面板升级标记协议（storage/upgrade/，web 进程写请求、upgrader 读执行）
# ============================================================

def upgrade_dir(base: str | Path | None = None) -> Path:
    d = Path(base) if base else Path(config.STORAGE_PATH)
    return d / "upgrade"


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_request(base: str | Path | None = None) -> dict | None:
    return _read_json(upgrade_dir(base) / "request.json")


def read_result(base: str | Path | None = None) -> dict | None:
    return _read_json(upgrade_dir(base) / "result.json")


def write_request(version: str, image: str, base: str | Path | None = None) -> dict:
    """写入升级请求（upgrader 轮询消费）。同目录已有未完成请求由调用方拦截。"""
    import uuid
    d = upgrade_dir(base)
    d.mkdir(parents=True, exist_ok=True)
    req = {
        "id": uuid.uuid4().hex[:12],
        "version": version,
        "image": image,
        "requested_at": int(time.time()),
    }
    (d / "request.json").write_text(
        json.dumps(req, ensure_ascii=False), encoding="utf-8")
    return req


def clear_request(base: str | Path | None = None) -> None:
    p = upgrade_dir(base) / "request.json"
    try:
        p.unlink()
    except OSError:
        pass


def write_heartbeat(base: str | Path | None = None) -> None:
    d = upgrade_dir(base)
    d.mkdir(parents=True, exist_ok=True)
    (d / "heartbeat.json").write_text(
        json.dumps({"ts": int(time.time())}), encoding="utf-8")


def upgrader_alive(base: str | Path | None = None) -> bool:
    """upgrader sidecar 是否在运行（心跳 60s 内视为存活）。"""
    hb = _read_json(upgrade_dir(base) / "heartbeat.json")
    return bool(hb and hb.get("ts") and time.time() - hb["ts"] < HEARTBEAT_MAX_AGE)


def read_upgrade_state(base: str | Path | None = None) -> dict:
    """合并 request/result 得到面板可展示的升级状态机。

    idle    无请求
    queued  请求已写入、upgrader 尚未接手（或 upgrader 未启用）
    running / success / failed  upgrader 已写结果（result.id == request.id）
    """
    req, res = read_request(base), read_result(base)
    if not req:
        if res:
            return {"state": res.get("state", "failed"), **res}
        return {"state": "idle"}
    if res and res.get("id") == req.get("id"):
        return {"state": res.get("state", "failed"), **res}
    return {"state": "queued", "target": req.get("version", ""), **req}
