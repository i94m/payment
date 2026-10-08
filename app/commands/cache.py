"""
缓存管理命令

用法:
    uv run artisan.py cache clear
"""

import typer

app = typer.Typer(help="管理应用缓存")


@app.command("clear")
def clear():
    """清除应用缓存"""
    try:
        import redis
        from app.core.config import config

        r = redis.Redis(
            host=config.REDIS_HOST,
            port=config.REDIS_PORT,
            password=config.REDIS_PASSWORD or None,
            db=0,
            decode_responses=True,
        )
        # 只清除带前缀的缓存 key，不影响其他数据
        prefix = config.CACHE_PREFIX
        if prefix:
            keys = r.keys(f"{prefix}*")
        else:
            keys = []
        if keys:
            r.delete(*keys)
            print(f"✅ 已清除 {len(keys)} 个缓存 key (prefix: {prefix or '无'})")
        else:
            print("⚠️  没有找到需要清除的缓存 key")
    except Exception as e:
        print(f"❌ 清除缓存失败: {e}")
        raise typer.Exit(1)
