"""版本升级辅助 — 查询镜像仓库可用版本。

供 ``scripts/deploy.sh upgrade`` 在容器内调用（服务器无需 python/jq）：

    docker compose exec -T app python artisan.py upgrade versions
    docker compose exec -T app python artisan.py upgrade check

后台「版本升级」页与 deploy.sh 共用 app/services/upgrade.py 的版本发现。
"""
import asyncio

import typer

from app.core.config import config
from app.services.upgrade import UpgradeError, fetch_available_versions, is_newer

app = typer.Typer(help="版本升级（镜像版本查询）")


def _versions() -> list[str]:
    return asyncio.run(fetch_available_versions())


@app.command("versions")
def versions():
    """列出镜像仓库可用版本，最新在最上（deploy.sh 逐行读取）。"""
    try:
        result = _versions()
    except UpgradeError as e:
        typer.echo(f"获取版本失败：{e}", err=True)
        raise typer.Exit(1)
    for v in result:
        typer.echo(v)


@app.command("check")
def check():
    """当前版本 vs 镜像仓库最新版。"""
    current = config.APP_VERSION
    typer.echo(f"当前版本: {current}（镜像 {config.PAY_IMAGE or '未知'}）")
    try:
        result = _versions()
    except UpgradeError as e:
        typer.echo(f"获取版本失败：{e}", err=True)
        raise typer.Exit(1)
    typer.echo(f"最新版本: {result[0]}" + ("（可升级）" if is_newer(result[0], current) else "（已是最新）"))
    typer.echo(f"可用版本: {'、'.join(result)}")
