import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


def get_logger() -> logging.Logger:
    """
    创建项目统一日志记录器。

    日志文件：
    logs/app.log

    单个日志文件最大2MB，最多保留5个历史文件。
    """

    logger = logging.getLogger("mine_diagnosis_assistant")

    # Streamlit每次刷新都会重新执行脚本，
    # 已经创建处理器时直接返回，防止日志重复。
    if logger.handlers:
        return logger

    level_name = os.getenv(
        "LOG_LEVEL",
        "INFO",
    ).upper()

    log_level = getattr(
        logging,
        level_name,
        logging.INFO,
    )

    logger.setLevel(log_level)
    logger.propagate = False

    project_dir = Path(__file__).resolve().parent
    log_dir = project_dir / "logs"
    log_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    log_file = log_dir / "app.log"

    formatter = logging.Formatter(
        fmt=(
            "%(asctime)s | "
            "%(levelname)s | "
            "%(name)s | "
            "%(message)s"
        ),
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        filename=log_file,
        maxBytes=2 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )

    file_handler.setLevel(log_level)
    file_handler.setFormatter(formatter)

    logger.addHandler(file_handler)

    return logger