from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, Request, Body
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from voxcall.webui.controller import VoxCallController, WebEventBus


def create_app(cfg_path: Path, version: str) -> FastAPI:
    bus = WebEventBus()
    ctrl = VoxCallController(cfg_path=cfg_path, version=version, bus=bus)

    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # event loop is now available
        bus.set_loop(asyncio.get_running_loop())
        app.state.shutdown_evt = asyncio.Event()

        # Always autostart in web mode
        ctrl.start()

        try:
            yield
        finally:
            # Make SSE loops exit quickly
            app.state.shutdown_evt.set()

            # Stop engine without blocking event loop
            try:
                await asyncio.to_thread(ctrl.stop)
            except Exception:
                pass

    app = FastAPI(title="VoxCall WebUI", lifespan=lifespan)

    app.mount(
        "/static",
        StaticFiles(directory=str(Path(__file__).parent / "static")),
        name="static",
    )

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse(
            "index.html",
            {"request": request, "version": version},
        )

    @app.get("/api/state")
    async def api_state():
        return JSONResponse(ctrl.get_state())

    @app.get("/api/config")
    async def api_config():
        return JSONResponse(ctrl.get_config())

    @app.get("/api/devices")
    async def api_devices():
        return JSONResponse(ctrl.list_devices())

    @app.post("/api/engine/start")
    async def api_start():
        ctrl.start()
        return JSONResponse({"ok": True, "state": ctrl.get_state()})

    @app.post("/api/engine/stop")
    async def api_stop():
        ctrl.stop()
        return JSONResponse({"ok": True, "state": ctrl.get_state()})

    # Autosave is already true because ctrl.patch_config() calls save_config()
    @app.patch("/api/config")
    async def api_patch_config(patch: Dict[str, Any] = Body(...)):
        ctrl.patch_config(patch)
        return JSONResponse({"ok": True, "config": ctrl.get_config(), "state": ctrl.get_state()})

    # Optional: a “Save Now” endpoint (safe even if autosave)
    @app.post("/api/config/save")
    async def api_save_config():
        # patch_config already saves, but if you want a no-op “save now” call:
        # just rewrite the current config file.
        from voxcall.config import save_config
        save_config(ctrl.cfg_path, ctrl.cfg)
        return JSONResponse({"ok": True})

    @app.get("/api/events")
    async def sse_events(request: Request):
        q = bus.add_client()

        async def gen():
            try:
                # initial snapshot (real JSON)
                yield f"event: state\ndata: {json.dumps(ctrl.get_state(), separators=(',', ':'))}\n\n"

                # Loop ends on shutdown OR disconnect
                while not request.app.state.shutdown_evt.is_set():
                    # If client disconnects, FastAPI/Starlette may not always cancel immediately;
                    # this timeout keeps the loop responsive.
                    try:
                        msg = await asyncio.wait_for(q.get(), timeout=1.0)
                        yield msg
                    except asyncio.TimeoutError:
                        # heartbeat + gives us a chance to check shutdown_evt
                        yield ": ping\n\n"
                    except asyncio.CancelledError:
                        break
            finally:
                try:
                    bus.remove_client(q)
                except Exception:
                    pass

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return app
