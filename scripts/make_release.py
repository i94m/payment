#!/usr/bin/env python3
"""生成客户部署包：交互询问文件夹名与授权到期日，产出 .env / compose / deploy 脚本并打 zip。

给客户的交付物（解压即用）：
  <文件夹名>/
  ├── .env                         已生成 APP_SECRET_KEY / STORAGE_LOCAL_SECRET / AUTH_CODE
  ├── docker-compose.yml
  ├── nginx-conf.conf              宝塔站点配置（直接粘贴到站点「配置文件」）
  ├── 安装说明.md                  改配置 → 运行命令 → 访问后台
  └── scripts/deploy.sh            拉镜像 / 起容器 / 建表或增量迁移 / 健康检查

用法：
  uv run python scripts/make_release.py
  # 非交互（CI/自动化）：
  uv run python scripts/make_release.py --name client-a --until 2027-12-31 \\
      --image ghcr.io/i94m/payment:1.2.3

产物默认输出到 dist/releases/<文件夹名>/ 与 dist/releases/<文件夹名>.zip
（dist/ 已 gitignore，不会入库；授权私钥 license_private.pem 同样只在作者本机）。
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import secrets
import shutil
import time
import zipfile
from datetime import date, datetime
from pathlib import Path

import typer

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_IMAGE = "ghcr.io/i94m/payment:latest"
DEFAULT_OUT = "dist/releases"

app = typer.Typer(add_completion=False, help="生成客户部署包（.env + compose + 部署脚本 + zip）")


# ============================================================
# 输入校验与交互
# ============================================================

def _check_name(value: str) -> str:
    """解压后的目录名：同时用作 zip 内顶层目录名。"""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", value):
        raise ValueError("文件夹名称需以字母/数字开头，仅含字母、数字、点、下划线、中划线，长度 ≤64")
    return value


def _check_until(value: str) -> str:
    try:
        d = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("有效日期格式应为 YYYY-MM-DD") from None
    if d <= date.today():
        raise ValueError("有效日期必须晚于今天")
    return value


def _prompt(label: str, validator, default: str | None = None) -> str:
    """交互输入并校验；不合法就重问。"""
    while True:
        raw = typer.prompt(label, default=default) if default is not None else typer.prompt(label)
        try:
            return validator(str(raw).strip())
        except ValueError as e:
            typer.secho(f"  {e}", fg="red", err=True)


# ============================================================
# 授权码签发（复用 license_gen.py，私钥不落地到产物）
# ============================================================

def _load_license_gen():
    spec = importlib.util.spec_from_file_location(
        "license_gen", ROOT / "scripts" / "license_gen.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _issue_code(gen, until: str, note: str) -> tuple[str, int]:
    """按 --until（当天 23:59:59）签发授权码，返回 (code, exp_ts)。"""
    key = gen._load_private_key()  # 私钥缺失时打印指引并以非 0 退出
    end_of_day = datetime.strptime(until, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    exp = int(end_of_day.timestamp())
    payload = {"exp": exp, "iat": int(time.time()), "sub": note}
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    payload_seg = gen._seg(payload_json)
    sig = key.sign(payload_seg.encode("ascii"))
    return f"{payload_seg}.{gen._seg(sig)}", exp


def _check_baked_pubkey(gen) -> str | None:
    """签发私钥与镜像内置公钥一致性检查（不一致=客户装上后仍是无授权状态）。"""
    src = (ROOT / "app" / "core" / "license.py").read_text(encoding="utf-8")
    m = re.search(r'PUBLIC_KEY_B64:\s*str\s*=\s*"([^"]+)"', src)
    if not m:
        return "未找到 app/core/license.py 的 PUBLIC_KEY_B64"
    if m.group(1).strip() != gen._pubkey_b64(gen._load_private_key()):
        return ("当前签发改用私钥与镜像内置公钥不一致：镜像需用对应公钥重建，"
                "否则客户部署后页面仍返回「Invalid License Code」")
    return None


# ============================================================
# 文件生成
# ============================================================

def _set_env(text: str, key: str, value: str) -> str:
    """替换 .env 里的 KEY=... 行；不存在则追加。"""
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    line = f"{key}={value}"
    if pattern.search(text):
        return pattern.sub(line, text, count=1)
    return text.rstrip("\n") + f"\n{line}\n"


def _guide(name: str, until: str, image: str) -> str:
    return f"""# {name} 部署包

本包由 `scripts/make_release.py` 生成，解压后按下面步骤操作即可。

## 1. 修改配置（只会用到 .env）

```shell
cd {name}
vi .env
```

必改（按服务器实际环境）：

| 配置 | 说明 |
|---|---|
| `DB_HOST` / `DB_PORT` / `DB_DATABASE` / `DB_USERNAME` / `DB_PASSWORD` | 宿主机 MySQL（需提前建库，字符集 utf8mb4） |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` | 宿主机 Redis |
| `APP_PORT_RANGE` / `APP_REPLICAS` | app 实例端口范围与数量（默认 8000-8002 / 3，按需调整） |
| `PAY_IMAGE` | 镜像版本（当前 `{image}`；升级/回滚只改这一行） |

**不要修改**（已生成，改了会导致登录异常或授权失效）：
`APP_SECRET_KEY`、`STORAGE_LOCAL_SECRET`、`AUTH_CODE`。

## 2. 启动

```shell
bash scripts/deploy.sh
```

脚本会：拉镜像 → 起容器 → 等健康检查 → 空库自动建表初始化（管理后台/admin、支付方式、
权限树、默认设置）或已有库自动增量迁移。可重复执行（升级 = 改 `PAY_IMAGE` 后再跑一次）。

服务器无法访问镜像仓库时：先在本机 `docker save` 镜像传到服务器 `docker load`，
再用 `bash scripts/deploy.sh --no-pull`。

## 3. 宝塔反向代理（推荐）

1. 宝塔「网站」→ 添加站点：域名填你的域名（如 `pay.example.com`），PHP 版本选「纯静态」
2. 打开该站点「配置文件」，全选清空后粘贴包内 `nginx-conf.conf`
3. 把文件里的 `pay.example.com` 全部替换成你的域名，保存（宝塔会自动检查配置）
4. 需要 HTTPS：站点设置 → SSL → 申请证书（证书配置会自动写入 `#SSL-START` / `#SSL-END` 之间）
5. 之后在宝塔「域名管理」增删域名，记得把本文件里的域名同步替换即可

非宝塔环境：把 `nginx-conf.conf` 放到 `/etc/nginx/conf.d/pay-gateway.conf`，
替换域名与 `/www/wwwroot`、`/www/wwwlogs` 路径后执行 `nginx -t && systemctl reload nginx`。

## 4. 访问

app 容器的端口只绑在本机 `127.0.0.1:8000-8002`，由第 3 步的 nginx 反代对外提供服务，
**不要用 `http://<服务器IP>:8000` 直接访问**（连不通）。统一走已配好的域名：

- 管理后台：`http://<你的域名>/admin`（首次 `admin/123456`，登录后立即改密）
- 授权到期查询：`http://<你的域名>/license/expire`（纯文本 `YYYY-MM-DD`）

## 5. 授权信息

- 授权对象：`{name}`
- 到期日期：`{until}`（当天 23:59:59 前有效；过期后业务接口 503，需换新授权码重启）

## 6. 目录说明

- `storage/`：持久化数据（日志/上传/导出/三方插件 `storage/plugins`），请勿删除
- 三方插件：把编译好的插件目录拷进 `storage/plugins/<插件名>/` 后，在后台「已安装插件」点一次刷新即可，无需重启容器/换镜像
- 升级：修改 `PAY_IMAGE` 后重新执行 `bash scripts/deploy.sh`；回滚 = 改回旧版本标签再执行
"""


def _write_package(release_dir: Path, name: str, until: str, image: str, code: str) -> None:
    release_dir.mkdir(parents=True)

    # .env：基于仓库 .env.example，替换密钥/授权/镜像
    env_text = (ROOT / ".env.example").read_text(encoding="utf-8")
    env_text = _set_env(env_text, "APP_SECRET_KEY", secrets.token_hex(32))
    env_text = _set_env(env_text, "STORAGE_LOCAL_SECRET", secrets.token_hex(32))
    env_text = _set_env(env_text, "AUTH_CODE", code)
    env_text = _set_env(env_text, "APP_ENV", "prod")
    env_text = _set_env(env_text, "APP_DEBUG", "false")
    env_text = _set_env(env_text, "PAY_IMAGE", image)
    env_path = release_dir / ".env"
    env_path.write_text(env_text, encoding="utf-8")
    env_path.chmod(0o600)  # 含密钥，仅所有者可读

    # 编排 + 部署脚本 + nginx 示例（客户包内编排统一命名为 docker-compose.yml）
    shutil.copy2(ROOT / "docker-compose.production.yml",
                 release_dir / "docker-compose.yml")
    (release_dir / "scripts").mkdir()
    deploy_src = ROOT / "scripts" / "deploy.sh"
    shutil.copy2(deploy_src, release_dir / "scripts" / "deploy.sh")
    (release_dir / "scripts" / "deploy.sh").chmod(0o755)
    shutil.copy2(ROOT / "nginx-conf.conf", release_dir / "nginx-conf.conf")

    # 持久化目录占位（挂载点，避免 docker 以 root 预创建导致后续无写权限）
    (release_dir / "storage").mkdir()

    (release_dir / "安装说明.md").write_text(
        _guide(name, until, image), encoding="utf-8")


def _make_zip(release_dir: Path, zip_path: Path) -> None:
    """zip 内顶层目录 = 文件夹名，解压即得 <文件夹名>/...。"""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(release_dir.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(release_dir.parent).as_posix())


# ============================================================
# 入口
# ============================================================

@app.command()
def main(
    name: str = typer.Option(None, "--name", help="待打包的文件夹名称（解压后的目录名）"),
    until: str = typer.Option(None, "--until", help="授权有效日期 YYYY-MM-DD（当天 23:59:59 到期）"),
    image: str = typer.Option(None, "--image", help=f"镜像标签（默认 {DEFAULT_IMAGE}）"),
    out: str = typer.Option(DEFAULT_OUT, "--out", help="输出目录（默认 dist/releases，已 gitignore）"),
    force: bool = typer.Option(False, "--force", help="覆盖已存在的同名产物"),
):
    """交互生成客户部署包并打 zip。"""
    # ---- 1. 交互补全 + 校验 ----
    if name is None:
        name = _prompt("待打包的文件夹名称（解压后的目录名）", _check_name)
    else:
        try:
            name = _check_name(name)
        except ValueError as e:
            raise typer.BadParameter(str(e)) from None

    if until is None:
        until = _prompt("授权有效日期（YYYY-MM-DD）", _check_until)
    else:
        try:
            until = _check_until(until)
        except ValueError as e:
            raise typer.BadParameter(str(e)) from None

    if not image:
        image = _prompt("镜像标签", lambda v: v or DEFAULT_IMAGE, default=DEFAULT_IMAGE)

    # ---- 2. 签发授权码 ----
    gen = _load_license_gen()
    if not gen.KEY_PATH.exists():
        typer.secho(f"缺少授权私钥：{gen.KEY_PATH}", fg="red", err=True)
        typer.secho("请先在作者本机运行: uv run python scripts/license_gen.py keygen", err=True)
        raise typer.Exit(1)
    warn = _check_baked_pubkey(gen)
    if warn:
        typer.secho(f"警告：{warn}", fg="yellow", err=True)
        if not force and not typer.confirm("仍要继续生成？", default=False):
            raise typer.Exit(1)
    code, _exp = _issue_code(gen, until, note=name)

    # ---- 3. 输出目录 ----
    out_dir = Path(out) if Path(out).is_absolute() else ROOT / out
    release_dir = out_dir / name
    zip_path = out_dir / f"{name}.zip"
    if release_dir.exists() or zip_path.exists():
        if not force and not typer.confirm(f"{release_dir} 已存在，覆盖？", default=False):
            raise typer.Exit(1)
        shutil.rmtree(release_dir, ignore_errors=True)
        zip_path.unlink(missing_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- 4. 生成文件并打包 ----
    typer.secho("生成部署文件...", fg="cyan")
    _write_package(release_dir, name, until, image, code)
    typer.secho("压缩 zip...", fg="cyan")
    _make_zip(release_dir, zip_path)

    typer.secho("\n完成 ✔", fg="green")
    typer.echo(f"  部署目录: {release_dir}")
    typer.echo(f"  交付压缩包: {zip_path}")
    typer.echo(f"  授权对象: {name}    到期: {until} 23:59:59")
    typer.echo(f"  镜像版本: {image}")
    typer.secho("  （dist/ 已 gitignore，产物不会入库；zip 直接发给客户解压使用）", dim=True)


if __name__ == "__main__":
    app()
