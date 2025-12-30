from __future__ import annotations
import os
import sys
from pathlib import Path

from voxcall.paths import app_dir
from voxcall.logging_setup import setup_logging
from voxcall.ui.app import VoxCallGui

def version_name() -> str:
    if getattr(sys, "frozen", False):
        return os.path.basename(sys.executable).split(".")[0]
    return os.path.basename(__file__).split(".")[0]

def main():
    base = app_dir()
    setup_logging(base / "log.txt")
    cfg_path = base / "config.cfg"
    app = VoxCallGui(cfg_path=cfg_path, version=version_name())
    app.run()

if __name__ == "__main__":
    main()
