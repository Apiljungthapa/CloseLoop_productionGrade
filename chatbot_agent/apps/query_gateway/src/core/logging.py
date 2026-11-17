import sys

from loguru import logger

from core.config import get_settings


def setup_logging(level: str | None = None) -> None:
    """Log to stderr. The level comes from LOG_LEVEL (.env) unless passed explicitly."""
    level = (level or get_settings().LOG_LEVEL).upper()
    logger.remove()
    logger.add(sys.stderr, level=level, format="{time:YYYY-MM-DD HH:mm:ss} | {level:<7} | {name}:{function} - {message}")
