from contextvars import ContextVar
import logging
from logging.handlers import RotatingFileHandler
import sys
from app.core.config import settings

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get("-")
        return True


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("wims")
    if getattr(logger, "_wims_configured", False):
        return logger

    settings.LOG_DIR.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    logger.setLevel(level)
    logger.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-7s | req=%(request_id)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    req_filter = RequestIdFilter()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(req_filter)

    file_handler = RotatingFileHandler(
        filename=settings.LOG_DIR / "wims.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(req_filter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    setattr(logger, "_wims_configured", True)
    return logger


logger = configure_logging()

