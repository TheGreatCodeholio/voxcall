#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

import uvicorn

from voxcall.logging_setup import setup_logging
from voxcall.config import load_config, save_config
from voxcall.engine import VoxCallEngine, UiHooks
from voxcall.ui.app import VoxCallGui
from voxcall.webui.app import create_app

# Optional but recommended for “config/log outside package”
try:
    from platformdirs import user_config_dir, user_state_dir
except Exception:  # pragma: no cover
    user_config_dir = None
    user_state_dir = None


APP_NAME = "voxcall"
APP_AUTHOR = "Thinline Dynamic Solutions"


def version_name() -> str:
    # Keep your existing behavior
    if getattr(sys, "frozen", False):
        return os.path.basename(sys.executable).split(".")[0]
    return APP_NAME


def default_data_dir() -> Path:
    """
    Where config/log live.
    - If VOXCALL_HOME is set: use that (portable / explicit override)
    - Otherwise use platformdirs (best UX on Windows/macOS/Linux)
    - Fallback: ~/.voxcall
    """
    env = os.getenv("VOXCALL_HOME")
    if env:
        p = Path(env).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    if user_config_dir and user_state_dir:
        # Put config + state in same folder for simplicity
        p = Path(user_config_dir(APP_NAME, APP_AUTHOR)).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    p = Path.home() / f".{APP_NAME}"
    p.mkdir(parents=True, exist_ok=True)
    return p


def ensure_config(cfg_path: Path) -> None:
    """
    If config doesn't exist, create a default one once.
    """
    if cfg_path.exists():
        return
    cfg = load_config(cfg_path)  # your load likely already creates defaults
    save_config(cfg_path, cfg)


def run_cli(cfg_path: Path, version: str) -> int:
    cfg = load_config(cfg_path)

    def _status(s: str):
        print(s, flush=True)

    hooks = UiHooks(
        set_status=_status,
        set_status_color=lambda _c: None,
        set_bar=lambda _v: None,
    )

    engine = VoxCallEngine(cfg, version=version, hooks=hooks)

    try:
        engine.run_forever()
        return 0
    except KeyboardInterrupt:
        print("Stopping…", flush=True)
        return 0
    finally:
        try:
            engine.stop()
        except Exception:
            pass


def run_gui(cfg_path: Path, version: str) -> int:
    import signal

    app = VoxCallGui(cfg_path=cfg_path, version=version, theme="darkly")

    # Install SIGINT handler so Ctrl-C shuts down cleanly (no traceback)
    old_handler = signal.getsignal(signal.SIGINT)

    def _handle_sigint(signum, frame):
        try:
            # Schedule on Tk thread
            app.root.after(0, app._exit)
        except Exception:
            try:
                app._exit()
            except Exception:
                pass

    try:
        signal.signal(signal.SIGINT, _handle_sigint)
    except Exception:
        # If signal handler can't be set (rare), we'll still catch KeyboardInterrupt below
        pass

    try:
        app.run()
        return 0
    except KeyboardInterrupt:
        # Fallback: if SIGINT became KeyboardInterrupt anyway
        try:
            app._exit()
        except Exception:
            pass
        return 0
    finally:
        # Restore previous handler so we don't affect other code/tests
        try:
            signal.signal(signal.SIGINT, old_handler)
        except Exception:
            pass


def run_web(cfg_path: Path, version: str, host: str, port: int, log_level: str) -> int:
    app = create_app(cfg_path=cfg_path, version=version)
    try:
        uvicorn.run(
            app,
            host=host,
            port=port,
            log_level=log_level,
            timeout_graceful_shutdown=1,  # small but nonzero helps it actually force-close
            timeout_keep_alive=1,
        )
        return 0
    except KeyboardInterrupt:
        return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=APP_NAME, description="VoxCall launcher")
    p.add_argument("--data-dir", type=Path, default=None, help="Override data directory for config/logs.")
    p.add_argument("--config", type=Path, default=None, help="Override config path directly.")
    p.add_argument("--log", type=Path, default=None, help="Override log path directly.")

    sub = p.add_subparsers(dest="mode", required=False)

    sub.add_parser("gui", help="Launch desktop UI.")

    w = sub.add_parser("web", help="Launch Web UI.")
    w.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1 for safety).")
    w.add_argument("--port", type=int, default=8765, help="Bind port (default: 8765).")
    w.add_argument("--uvicorn-log-level", default="info", help="Uvicorn log level.")

    sub.add_parser("cli", help="Run headless in the terminal (no UI).")

    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    base = (args.data_dir or default_data_dir()).expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True)

    cfg_path = (args.config or (base / "config.cfg")).resolve()
    log_path = (args.log or (base / "log.txt")).resolve()

    setup_logging(log_path)
    ensure_config(cfg_path)

    v = version_name()

    # If user double-clicks exe with no args, default to GUI (nice UX).
    mode = args.mode or os.getenv("VOXCALL_MODE") or "gui"
    mode = mode.lower().strip()

    if mode == "gui":
        return run_gui(cfg_path, v)
    if mode == "web":
        host = getattr(args, "host", "127.0.0.1")
        port = getattr(args, "port", 8765)
        ll = getattr(args, "uvicorn_log_level", "info")
        return run_web(cfg_path, v, host=host, port=port, log_level=ll)
    if mode == "cli":
        return run_cli(cfg_path, v)

    print(f"Unknown mode: {mode!r}. Use: gui | web | cli", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
