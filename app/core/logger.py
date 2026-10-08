import logging
import logging.handlers
from app.core.config import config

logger = logging.getLogger()
logger.setLevel(logging.DEBUG if config.APP_DEBUG else logging.INFO)
formatter = logging.Formatter("%(levelname)s %(asctime)s %(pathname)s\n%(message)s")

# 输出到文件中（按天轮转，保留30天）
from pathlib import Path
log_dir = Path("storage/logs")
log_dir.mkdir(parents=True, exist_ok=True)

to_file = logging.handlers.TimedRotatingFileHandler(
    filename=str(log_dir / "app.log"),
    when="midnight",
    interval=1,
    backupCount=30,
    encoding="utf-8",
)
to_file.setFormatter(formatter)
to_file.suffix = "%Y-%m-%d"
logger.addHandler(to_file)

# 如需输出到控制台，取消注释以下两行
# to_console = logging.StreamHandler()
# to_console.setFormatter(formatter)
# logger.addHandler(to_console)
