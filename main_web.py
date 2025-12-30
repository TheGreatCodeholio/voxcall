from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn

from voxcall.paths import app_dir
from voxcall.logging_setup import setup_logging
from voxcall.webui.app import create_app


def version_name() -> str:
    if getattr(sys, "frozen", False):
        return os.path.basename(sys.executable).split(".")[0]
    return "voxcall"


def main():
    base = app_dir()
    setup_logging(base / "log.txt")

    cfg_path = base / "config.cfg"
    app = create_app(cfg_path=cfg_path, version=version_name())

    # bind 127.0.0.1 by default for safety; change to 0.0.0.0 for LAN
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")


if __name__ == "__main__":
    main()
