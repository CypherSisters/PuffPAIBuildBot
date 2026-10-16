"""
PuffPAI Logging Configuration
==============================
Centralized logging setup for all PuffPAI modules.

Usage:
    from puffpai.logging_config import setup_logging

    setup_logging()           # INFO to console
    setup_logging("DEBUG")    # DEBUG to console
    setup_logging("INFO", log_file="puffpai.log")  # console + rotating file

Modules should use:
    import logging
    logger = logging.getLogger(__name__)
    logger.info("message")
"""

import logging
import sys
from logging.handlers import RotatingFileHandler


# Logger hierarchy mirrors package structure:
#   puffpai
#   puffpai.security
#   puffpai.inference
#   puffpai.voice
#   puffpai.ui

_MAX_FILE_BYTES = 5 * 1024 * 1024   # 5 MB
_BACKUP_COUNT = 3
_CONSOLE_FORMAT = "[%(levelname).1s] %(name)s: %(message)s"
_FILE_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging(
    level: str = "INFO",
    log_file: str | None = None,
    force: bool = False,
) -> logging.Logger:
    """Configure logging for PuffPAI.

    Args:
        level: Log level string ("DEBUG", "INFO", "WARNING", "ERROR").
        log_file: Optional path for rotating file handler.
        force: Reconfigure even if already configured.

    Returns:
        The root 'puffpai' logger.
    """
    global _configured
    if _configured and not force:
        return logging.getLogger("puffpai")

    root_logger = logging.getLogger("puffpai")
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Clear existing handlers to avoid duplicates on reconfigure
    root_logger.handlers.clear()

    # Console handler (stderr)
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.DEBUG)
    console.setFormatter(logging.Formatter(_CONSOLE_FORMAT))
    root_logger.addHandler(console)

    # Optional rotating file handler. Never let file trouble (locked by a
    # second instance mid-rotation, permissions) take logging down with
    # it — console output must survive.
    if log_file:
        try:
            file_handler = RotatingFileHandler(
                log_file, maxBytes=_MAX_FILE_BYTES, backupCount=_BACKUP_COUNT,
                encoding="utf-8",
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(
                logging.Formatter(_FILE_FORMAT, datefmt=_DATE_FORMAT)
            )
            root_logger.addHandler(file_handler)
        except OSError as exc:
            root_logger.warning(
                "File logging disabled (%s): %s", log_file, exc)

    _configured = True
    return root_logger


def get_logger(name: str) -> logging.Logger:
    """Get a logger under the puffpai hierarchy.

    Usage:
        logger = get_logger(__name__)  # e.g. "puffpai.security.vault"
    """
    if not name.startswith("puffpai"):
        name = f"puffpai.{name}"
    return logging.getLogger(name)
