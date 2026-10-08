"""
密钥生成命令 — 生成 APP_SECRET_KEY 并写入 .env 文件

用法:
    uv run artisan.py key generate
"""

import secrets
import typer

app = typer.Typer(help="管理 APP_SECRET_KEY")


@app.command("generate")
def generate():
    """生成随机密钥并写入 .env 文件"""
    secret_key = secrets.token_hex(32)
    storage_key = secrets.token_hex(16)
    print(f"APP_SECRET_KEY: {secret_key}")
    print(f"STORAGE_LOCAL_SECRET: {storage_key}")

    try:
        with open(".env", "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        print("\n.env 文件不存在，请先执行:")
        print("  cp .env.example .env")
        print(f"\n然后将以下内容添加到 .env 中:")
        print(f'  APP_SECRET_KEY="{secret_key}"')
        print(f'  STORAGE_LOCAL_SECRET="{storage_key}"')
        return

    updated_keys = 0
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("APP_SECRET_KEY="):
            new_lines.append(f'APP_SECRET_KEY="{secret_key}"\n')
            updated_keys += 1
        elif stripped.startswith("STORAGE_LOCAL_SECRET="):
            new_lines.append(f'STORAGE_LOCAL_SECRET="{storage_key}"\n')
            updated_keys += 1
        else:
            new_lines.append(line)

    if updated_keys == 0:
        new_lines.append(f'APP_SECRET_KEY="{secret_key}"\n')
        new_lines.append(f'STORAGE_LOCAL_SECRET="{storage_key}"\n')

    with open(".env", "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    print(f"\n已更新 {updated_keys} 个密钥到 .env 文件")
