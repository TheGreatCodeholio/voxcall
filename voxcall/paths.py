import sys
from pathlib import Path

def app_dir() -> Path:
    # where the exe lives (frozen) OR project root (script)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent

def resource_path(rel: str) -> Path:
    return app_dir() / rel
