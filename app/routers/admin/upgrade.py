"""admin 版本升级接口：检查更新 / 触发一键升级 / 查询进度。

升级动作不发生在 web 进程（app 容器没有 docker.sock）：POST /upgrade/run
只往 storage/upgrade/ 写请求文件，由可选的 upgrader sidecar
（compose profile ``upgrade``，app/upgrader.py）轮询执行拉镜像/迁移/逐台重建。

未启用 upgrader 时仍可「检查更新」，页面会给出 ``deploy.sh upgrade`` 命令行
升级指引；启用了则一键完成。整机级操作（upgrader 持 docker.sock），与
账号管理一致仅超管可调（require_super_admin，子账号拿到权限键也进不来）。
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.config import config
from app.core.security import require_super_admin

_SUPER = [Depends(require_super_admin)]
from app.responses import err, suc
from app.services.upgrade import (
    UpgradeError,
    fetch_available_versions,
    image_with_tag,
    is_newer,
    read_upgrade_state,
    upgrader_alive,
    write_request,
)

router = APIRouter()


@router.get("/check", dependencies=_SUPER)
async def upgrade_check():
    """当前版本 vs 镜像仓库最新版；顺带返回升级执行器与进行中状态。"""
    current = config.APP_VERSION
    latest, versions, registry_error = "", [], ""
    try:
        versions = await fetch_available_versions()
        latest = versions[0] if versions else ""
    except UpgradeError as e:
        registry_error = str(e)
    return suc({
        "current": current,
        "latest": latest,
        "has_update": is_newer(latest, current),
        "versions": versions[:10],
        "image": config.PAY_IMAGE or "",
        "registry_error": registry_error,
        "upgrader": upgrader_alive(),
        "status": read_upgrade_state(),
    })


class UpgradeRunRequest(BaseModel):
    version: str = ""  # 空 = 升到最新；回滚可指定更低的已发布版本


@router.post("/run", dependencies=_SUPER)
async def upgrade_run(body: UpgradeRunRequest):
    state = read_upgrade_state()
    # running = 执行器正在处理该请求，拒绝并发；queued 允许覆盖提交
    # （request.json 原子覆盖；执行器完成手头任务后会接手最新请求）
    if state["state"] == "running":
        return err(-1, "已有升级任务在执行，请等待完成后再试")
    if not upgrader_alive():
        return err(-1, "一键升级未启用（需 upgrader 服务，见 DEPLOY.md「版本升级」）；"
                       "可在服务器执行 bash scripts/deploy.sh upgrade")
    try:
        versions = await fetch_available_versions()
    except UpgradeError as e:
        return err(-1, str(e))
    target = (body.version or "").strip() or (versions[0] if versions else "")
    if not target or target not in versions:
        return err(-1, f"版本 {target or '(空)'} 不在可用列表中，请刷新后重试")
    try:
        image = image_with_tag(target)
    except UpgradeError as e:
        return err(-1, str(e))
    req = write_request(target, image)
    return suc({"id": req["id"], "version": target, "image": image})


@router.get("/status", dependencies=_SUPER)
async def upgrade_status():
    """升级任务状态（面板轮询）：queued → running → success/failed。"""
    return suc({
        "current": config.APP_VERSION,
        "upgrader": upgrader_alive(),
        **read_upgrade_state(),
    })
