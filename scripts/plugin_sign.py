#!/usr/bin/env python3
"""插件包签名工具（作者专用，仅本地运行）。

背景：后台上传安装插件 = 在应用进程里执行 .so，等价于任意代码执行。编译镜像会
强制校验上传包内 `signature.json`（Ed25519 签名），未签名的包上传被拒；
手工拷进 `storage/plugins/` 的插件默认信任、不校验。
签名私钥就是授权码那把 `license_private.pem`（只在作者本机，已 gitignore）。

用法:
  # 签名（scripts/build_plugin.sh 打包后会自动调用，一般不用手跑）
  uv run python scripts/plugin_sign.py sign dist/plugin_packages/<名>.zip --version 1.0.0

  # 本地校验（发出去前自检，确认包能被线上接受）
  uv run python scripts/plugin_sign.py verify dist/plugin_packages/<名>.zip

  # 打印公钥（应与 app/core/plugin_signing.py 的 PLUGIN_PUBLIC_KEY_B64 一致）
  uv run python scripts/plugin_sign.py pubkey

产物：zip 内新增/覆盖 `<插件名>/signature.json`：
  {"payload": "<b64url(payload_json)>", "sig": "<b64url(ed25519_sig)>"}
  payload = {"name","sha256","iat","version"}，签名覆盖 "PLUGIN1:<payload 段>"。
"""
import base64
import json
import pathlib
import sys
import tempfile
import zipfile

import typer
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.core import plugin_signing as ps  # noqa: E402

KEY_PATH = pathlib.Path(__file__).resolve().parent.parent / "license_private.pem"

app = typer.Typer(help="插件包签名 / 验签（作者专用）", no_args_is_help=True)


def _load_private_key(path: pathlib.Path) -> Ed25519PrivateKey:
    if not path.exists():
        typer.secho(f"私钥不存在：{path}", fg="red", err=True)
        typer.secho("私钥只存作者本机（license_private.pem，已 gitignore）", fg="red", err=True)
        raise typer.Exit(1)
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        typer.secho("私钥类型不是 Ed25519，无法用于插件包签名", fg="red", err=True)
        raise typer.Exit(1)
    return key


def _find_plugin(zf: zipfile.ZipFile) -> tuple[str, str]:
    """返回 (插件名, 主模块 .so 在包内的路径)。"""
    entries = [i for i in zf.infolist() if not i.is_dir()]
    if not entries:
        raise typer.BadParameter("压缩包为空")
    if any(i.filename.endswith(".py") for i in entries):
        raise typer.BadParameter("包内包含 .py：上传接口会拒绝（仅接受编译后的 .so）")
    so_files = [i.filename for i in entries
                if i.filename.rsplit("/", 1)[-1].startswith("plugin")
                and i.filename.endswith(".so")]
    if not so_files:
        raise typer.BadParameter("包内未找到 plugin*.so（请用 scripts/build_plugin.sh 打包）")
    main = min(so_files, key=lambda f: f.count("/"))
    name = main.rsplit("/", 1)[0] if "/" in main else ""
    if not name:
        raise typer.BadParameter("包内 .so 必须在 <插件名>/ 目录下（后台上传按目录名取插件名）")
    return name.rsplit("/", 1)[-1], main


def _sha256_of_entry(zf: zipfile.ZipFile, entry: str) -> str:
    import hashlib
    digest = hashlib.sha256()
    with zf.open(entry) as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _public_key_b64(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


@app.command()
def sign(
    package: pathlib.Path = typer.Argument(..., exists=True, help="插件 zip（build_plugin.sh 产物）"),
    version: str = typer.Option("", "--version", help="插件版本（写进载荷便于审计，可留空）"),
    key: pathlib.Path = typer.Option(KEY_PATH, "--key", help="Ed25519 私钥 PEM"),
):
    """给插件包签名（写入 <插件名>/signature.json，覆盖已有签名）。"""
    private_key = _load_private_key(key)
    with zipfile.ZipFile(package) as zf:
        name, so_entry = _find_plugin(zf)
        sha256 = _sha256_of_entry(zf, so_entry)
        entries = [(i, zf.read(i.filename)) for i in zf.infolist()]

    payload = ps.build_payload(name, sha256, version=version)
    doc = ps.sign_payload(payload, private_key)
    sign_name = f"{name}/{ps.SIGN_FILE}"
    blob = json.dumps(doc, separators=(",", ":")).encode("utf-8")

    # 重写包：保留原条目顺序，替换/追加 signature.json
    tmp = pathlib.Path(tempfile.mkstemp(suffix=".zip", dir=str(package.parent))[1])
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
            for info, data in entries:
                if info.filename == sign_name:
                    continue
                out.writestr(info, data)
            out.writestr(sign_name, blob)
        tmp.replace(package)
    finally:
        tmp.unlink(missing_ok=True)

    typer.secho(f"已签名：{package}", fg="green")
    typer.echo(f"  插件名：{name}")
    typer.echo(f"  摘要  ：{sha256}")
    typer.echo(f"  版本  ：{version or '(未填写)'}")
    typer.echo(f"  公钥  ：{_public_key_b64(private_key)}")
    typer.secho(f"  签名文件：{sign_name}", fg="green")


@app.command()
def verify(
    package: pathlib.Path = typer.Argument(..., exists=True, help="插件 zip"),
    pubkey: str = typer.Option("", "--pubkey", help="覆盖内置公钥（默认用 app/core/plugin_signing.py 的）"),
):
    """本地验签（模拟线上校验），发布前自检用。"""
    with zipfile.ZipFile(package) as zf:
        name, so_entry = _find_plugin(zf)
        sha256 = _sha256_of_entry(zf, so_entry)
        sign_name = f"{name}/{ps.SIGN_FILE}"
        if sign_name not in zf.namelist():
            typer.secho(f"未签名：包内缺少 {sign_name}", fg="red")
            raise typer.Exit(1)
        doc = json.loads(zf.read(sign_name))

    try:
        payload = ps.verify_signature_doc(doc, pubkey or None)
    except ps.PackageSignatureError as e:
        typer.secho(f"验签失败：{e}", fg="red")
        raise typer.Exit(1)

    if payload.get("name") != name:
        typer.secho(f"验签失败：签名插件名 {payload.get('name')} 与目录名 {name} 不一致", fg="red")
        raise typer.Exit(1)
    if str(payload.get("sha256", "")).lower() != sha256:
        typer.secho("验签失败：.so 摘要与签名不一致（包被改动）", fg="red")
        raise typer.Exit(1)

    typer.secho("验签通过", fg="green")
    typer.echo(f"  插件名：{name}")
    typer.echo(f"  摘要  ：{sha256}")
    typer.echo(f"  版本  ：{payload.get('version') or '(未填写)'}")
    typer.echo(f"  签名时间：{payload.get('iat')}")


@app.command()
def pubkey(
    key: pathlib.Path = typer.Option(KEY_PATH, "--key", help="Ed25519 私钥 PEM"),
):
    """打印私钥对应的公钥（应与 app/core/plugin_signing.py 的内置公钥一致）。"""
    private_key = _load_private_key(key)
    b64 = _public_key_b64(private_key)
    typer.echo(b64)
    if b64 == ps.PLUGIN_PUBLIC_KEY_B64.strip():
        typer.secho("与镜像内置公钥一致 ✔", fg="green")
    else:
        typer.secho("与镜像内置公钥不一致！镜像里的包会验签失败，请核对/更新常量", fg="yellow")


if __name__ == "__main__":
    app()
