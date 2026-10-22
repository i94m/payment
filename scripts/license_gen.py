"""授权码签发工具（作者专用，仅本地运行）。

私钥 license_private.pem 只存作者本机（已 gitignore），绝不入库/进镜像/外发。
签发流程：
  1. uv run python scripts/license_gen.py keygen        # 首次：生成密钥对
  2. 把打印出的公钥粘贴进 app/core/license.py 的 PUBLIC_KEY_B64（唯一公钥来源，
     随 Cython 编译进镜像 .so；换钥需重建镜像并让各部署重新拉取）
  3. uv run python scripts/license_gen.py issue --until 2027-12-31 --note 客户A
  4. 把打印出的一行授权码交给对方，填入其 .env 的 AUTH_CODE

本脚本不 import app 任何模块，可独立于项目环境运行（仅需 cryptography）。
"""
import base64
import json
import time
from datetime import datetime
from pathlib import Path

import typer
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

KEY_PATH = Path(__file__).resolve().parent.parent / "license_private.pem"

app = typer.Typer(help="授权码签发（作者专用）", no_args_is_help=True)


def _load_private_key() -> Ed25519PrivateKey:
    if not KEY_PATH.exists():
        typer.secho(f"私钥不存在：{KEY_PATH}", fg="red", err=True)
        typer.secho("请先运行: uv run python scripts/license_gen.py keygen", err=True)
        raise typer.Exit(1)
    key = serialization.load_pem_private_key(KEY_PATH.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        typer.secho("私钥类型不是 Ed25519，请重新 keygen", fg="red", err=True)
        raise typer.Exit(1)
    return key


def _pubkey_b64(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def _seg(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


@app.command()
def keygen(force: bool = typer.Option(False, "--force", help="覆盖已存在的私钥")):
    """生成 Ed25519 密钥对：私钥写 license_private.pem，公钥打印出来填入 license.py。"""
    if KEY_PATH.exists() and not force:
        typer.secho(f"私钥已存在：{KEY_PATH}（覆盖请加 --force，旧授权码将全部失效）",
                    fg="yellow", err=True)
        raise typer.Exit(1)
    key = Ed25519PrivateKey.generate()
    KEY_PATH.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    KEY_PATH.chmod(0o600)
    typer.secho(f"私钥已生成：{KEY_PATH}（请妥善保管，绝不入库/进镜像）", fg="green")
    _print_pubkey(key)


@app.command()
def pubkey():
    """从已有私钥重印公钥（粘贴指引）。"""
    _print_pubkey(_load_private_key())


def _print_pubkey(key: Ed25519PrivateKey) -> None:
    pub = _pubkey_b64(key)
    typer.secho("\n公钥（唯一用法）：粘贴到 app/core/license.py 的 PUBLIC_KEY_B64，\n"
                "随 Cython 编译进镜像 .so（换钥需重建镜像并让各部署重新拉取）\n",
                fg="cyan")
    typer.echo(pub)
    typer.secho('\nPUBLIC_KEY_B64: str = "%s"' % pub, dim=True)


@app.command()
def issue(
    until: str = typer.Option(None, "--until", help="有效期至 YYYY-MM-DD（当天 23:59:59）"),
    days: int = typer.Option(None, "--days", min=1, help="有效天数（自现在起算；与 --until 二选一）"),
    note: str = typer.Option("", "--note", help="授权对象备注（客户名等，仅展示）"),
):
    """签发一行授权码（含有效期），直接发给对方填入 .env 的 AUTH_CODE。"""
    if bool(until) == bool(days):
        raise typer.BadParameter("--until 与 --days 必须二选一")
    now = int(time.time())
    if until:
        try:
            end_of_day = datetime.strptime(until, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59)
        except ValueError:
            raise typer.BadParameter("--until 格式应为 YYYY-MM-DD") from None
        exp = int(end_of_day.timestamp())
    else:
        exp = now + days * 86400

    payload = {"exp": exp, "iat": now, "sub": note}
    payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    payload_seg = _seg(payload_json)
    sig = _load_private_key().sign(payload_seg.encode("ascii"))
    # 裸码两段（载荷.签名），无前缀；旧版 PAY1. 前缀码校验侧仍兼容
    code = f"{payload_seg}.{_seg(sig)}"

    typer.secho("\n授权码（填入 .env 的 AUTH_CODE=）：\n", fg="cyan")
    typer.echo(code)
    typer.secho(f"\n有效期至：{time.strftime('%Y-%m-%d %H:%M', time.localtime(exp))}"
                f"（授权对象：{note or '默认'}）", fg="green")


if __name__ == "__main__":
    app()
