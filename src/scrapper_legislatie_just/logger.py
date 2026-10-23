import os
import sys
from pathlib import Path

from loguru import logger

_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
)


def configure_logging(level: str | None = None) -> None:
    """Configure console (and optional file) logging.

    The level is taken from the argument, then ``SCRAPPER_LOG_LEVEL`` and
    finally defaults to ``INFO``. Set ``SCRAPPER_LOG_FILE`` to also write a
    rotating log file.
    """
    level = (level or os.getenv("SCRAPPER_LOG_LEVEL") or "INFO").upper()

    logger.remove()
    logger.add(sys.stderr, level=level, format=_FORMAT, colorize=True)

    log_file = os.getenv("SCRAPPER_LOG_FILE")
    if log_file:
        path = Path(log_file).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            path,
            level=level,
            format=_FORMAT,
            rotation="100 MB",
            retention=None,
            enqueue=True,
        )


configure_logging()

log = logger
