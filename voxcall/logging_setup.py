import logging
from pathlib import Path

def setup_logging(log_path: Path) -> logging.Logger:
    logger = logging.getLogger("")  # root logger
    logger.setLevel(logging.DEBUG)

    # avoid duplicate handlers if called twice
    if logger.handlers:
        return logger

    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    ch.setFormatter(fmt)

    fh = logging.FileHandler(str(log_path), mode="w", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    logger.addHandler(ch)
    logger.addHandler(fh)
    return logger
