from contextvars import ContextVar
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
import threading
from app.core.config import settings

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get("-")
        return True


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("wims")
    settings.LOG_DIR.mkdir(parents=True, exist_ok=True)
    target_log_file = (settings.LOG_DIR / "wims.log").resolve()

    if getattr(logger, "_wims_configured", False):
        for handler in list(logger.handlers):
            if isinstance(handler, RotatingFileHandler):
                if Path(handler.baseFilename).resolve() != target_log_file:
                    logger.removeHandler(handler)
                    handler.close()
                    new_fh = RotatingFileHandler(
                        filename=target_log_file,
                        maxBytes=5 * 1024 * 1024,
                        backupCount=5,
                        encoding="utf-8",
                    )
                    new_fh.setLevel(handler.level)
                    if handler.formatter:
                        new_fh.setFormatter(handler.formatter)
                    for flt in handler.filters:
                        new_fh.addFilter(flt)
                    logger.addHandler(new_fh)
        return logger
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
    logger._wims_configured = True  # type: ignore[attr-defined]
    return logger


logger = configure_logging()


def install_global_exception_logging() -> None:
    """Route uncaught process, thread, and uvicorn.error exceptions into wims.log."""
    wims_logger = configure_logging()
    file_handlers = [h for h in wims_logger.handlers if isinstance(h, RotatingFileHandler)]
    if file_handlers:
        fh = file_handlers[0]
        for target_name in ("uvicorn.error", "asyncio"):
            ext_logger = logging.getLogger(target_name)
            if fh not in ext_logger.handlers:
                ext_logger.addHandler(fh)

    if getattr(sys, "_wims_excepthook_installed", False):
        return

    prev_excepthook = sys.excepthook

    def _sys_excepthook(exc_type, exc_value, exc_tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            wims_logger.info("WIMS received KeyboardInterrupt; shutting down.")
        else:
            wims_logger.critical("Uncaught fatal exception", exc_info=(exc_type, exc_value, exc_tb))
        prev_excepthook(exc_type, exc_value, exc_tb)

    sys.excepthook = _sys_excepthook

    if hasattr(threading, "excepthook"):
        prev_thread_hook = threading.excepthook

        def _thread_excepthook(args: threading.ExceptHookArgs) -> None:
            wims_logger.error(
                "Uncaught exception in thread %s",
                getattr(args.thread, "name", "unknown"),
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
            prev_thread_hook(args)

        threading.excepthook = _thread_excepthook

    sys._wims_excepthook_installed = True  # type: ignore[attr-defined]

