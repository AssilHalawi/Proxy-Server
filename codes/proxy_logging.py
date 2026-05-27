# ==================================================
# file: proxy_logging.py
# primary contributor: reina harake
# contributions:
# - logger configuration (file handler, formatter)
# - consistent log format used across all proxy modules
# team support:
# - assil halawi (logger consumed in proxy_server.py)
# ==================================================

import logging


def build_logger(log_path: str = "proxy.log") -> logging.Logger:
    """
    reina harake: build and return the shared proxy logger.

    Always uses the logger name "proxy" so different modules share the
    same logger instance without creating duplicate handlers.
    Writes INFO-level and above to a UTF-8 encoded log file.
    """
    # use a fixed name so repeated calls return the same logger
    logger = logging.getLogger("proxy")
    logger.setLevel(logging.INFO)

    # reina harake: guard against adding duplicate handlers if this is called more than once
    if logger.handlers:
        return logger

    # write to a file so logs persist across the session and can be shown in the admin ui
    handler = logging.FileHandler(log_path, encoding="utf-8")
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger
