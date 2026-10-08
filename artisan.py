#!/usr/bin/env python
import importlib
import os
import sys
import typer

app = typer.Typer(help="Artisan command runner")


def get_commands():
    # key generate 负责写入 APP_SECRET_KEY，而其余命令模块在导入期即实例化
    # Config（要求密钥已存在）。调用 key 子命令时只导入 key 模块，
    # 否则初始化第一步就会因密钥缺失而失败（鸡生蛋问题）。
    only_key = len(sys.argv) > 1 and sys.argv[1] == "key"
    modules = []
    for file in os.listdir('app/commands'):
        if file.endswith('.py') and not file.startswith('__') and not file.startswith('pydantic'):
            module_name = file[:-3]
            if only_key and module_name != 'key':
                continue
            module = importlib.import_module(f'app.commands.{module_name}')
            modules.append(module)
    return modules


# 动态注册所有命令模块
for m in get_commands():
    # 每个命令模块应暴露一个 Typer 实例变量名为 app
    if hasattr(m, 'app'):
        # 使用模块名作为命令名称
        command_name = m.__name__.split('.')[-1]
        app.add_typer(m.app, name=command_name)


# 如果没有任何子命令，给出友好的提示，避免 Typer 抛出 RuntimeError
@app.callback()
def main():
    """Artisan 命令行工具。使用 --help 查看可用子命令。"""
    # 这里无需实现逻辑，Typer 会处理 help 展示
    pass


if __name__ == "__main__":
    app()
