from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from voxcall.config import load_config, save_config
from voxcall.audio.devices import list_input_devices
from voxcall.engine import VoxCallEngine, UiHooks


@dataclass
class LiveState:
    running: bool = False
    status_text: str = "STANDBY"

    led_rx: bool = False
    led_rec: bool = False

    level_pct: int = 0
    level_db: Optional[float] = None

    sql_threshold: int = 75

    updated_ts: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # make JSON nicer
        d["level_db"] = None if self.level_db is None else float(self.level_db)
        return d


class WebEventBus:
    """
    Simple SSE broadcaster:
      - FastAPI event loop owns queues
      - engine thread can emit events thread-safely
    """
    def __init__(self):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._queues: list[asyncio.Queue[str]] = []
        self._lock = threading.Lock()

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    def add_client(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=200)
        with self._lock:
            self._queues.append(q)
        return q

    def remove_client(self, q: asyncio.Queue[str]):
        with self._lock:
            if q in self._queues:
                self._queues.remove(q)

    def emit(self, event: str, payload: Dict[str, Any]):
        if not self._loop:
            return
        data = f"event: {event}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"

        def _fanout():
            # called in event loop thread
            dead = []
            for q in list(self._queues):
                try:
                    q.put_nowait(data)
                except asyncio.QueueFull:
                    # drop oldest-ish behavior: just drop this event for this client
                    pass
                except Exception:
                    dead.append(q)
            for q in dead:
                self.remove_client(q)

        try:
            self._loop.call_soon_threadsafe(_fanout)
        except Exception:
            pass


class VoxCallController:
    DB_MIN = -80.0
    DB_MAX = 0.0

    def __init__(self, cfg_path: Path, version: str, bus: WebEventBus):
        self.cfg_path = cfg_path
        self.version = version
        self.bus = bus

        self.cfg = load_config(cfg_path)

        # devices (cached)
        self.input_devices, self.name_to_index, self.index_to_name = list_input_devices()

        self._lock = threading.Lock()
        self.state = LiveState(
            running=False,
            status_text="STANDBY",
            led_rx=False,
            led_rec=False,
            level_pct=0,
            level_db=None,
            sql_threshold=int(getattr(self.cfg.audio, "record_threshold", 75) or 0),
            updated_ts=time.time(),
        )

        self.engine: Optional[VoxCallEngine] = None
        self.engine_thread: Optional[threading.Thread] = None
        self._squelch_open = False
        self._sql_hyst = 3

    # ---------- level normalization (same behavior as GUI) ----------

    def _db_to_percent(self, db: float) -> int:
        clipped = max(self.DB_MIN, min(self.DB_MAX, db))
        pct = int(round((clipped - self.DB_MIN) / (self.DB_MAX - self.DB_MIN) * 100.0))
        return max(0, min(100, pct))

    def _normalize_level(self, v: Any) -> Tuple[int, Optional[float]]:
        try:
            raw = float(v)
        except Exception:
            return 0, None

        if 0.0 <= raw <= 1.5:
            pct = int(round(raw * 100.0))
            return max(0, min(100, pct)), None

        if raw < 0.0:
            return self._db_to_percent(raw), raw

        pct = int(round(raw))
        return max(0, min(100, pct)), None

    # ---------- engine hooks ----------

    def _set_status(self, text: str):
        with self._lock:
            self.state.status_text = (text or "").strip()[:64]
            self.state.updated_ts = time.time()
        self.bus.emit("state", self.state.to_dict())

    def _set_status_color(self, color: str):
        # keep your semantics: red/green affect REC LED
        with self._lock:
            if color == "red":
                self.state.led_rec = True
            elif color == "green":
                self.state.led_rec = True
            else:
                self.state.led_rec = False
            self.state.updated_ts = time.time()
        self.bus.emit("state", self.state.to_dict())

    def _set_level(self, v: Any):
        pct, db = self._normalize_level(v)

        with self._lock:
            self.state.level_pct = pct
            self.state.level_db = db

            thr = max(0, min(100, int(self.state.sql_threshold or 0)))

            if thr == 0:
                open_now = True
            else:
                if self._squelch_open:
                    open_now = pct >= max(0, thr - self._sql_hyst)
                else:
                    open_now = pct >= min(100, thr + self._sql_hyst)

            self._squelch_open = open_now
            self.state.led_rx = open_now
            self.state.updated_ts = time.time()

        self.bus.emit("state", self.state.to_dict())

    # ---------- public API ----------

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            return self.state.to_dict()

    def list_devices(self) -> Dict[str, Any]:
        return {
            "devices": self.input_devices,
            "current": self.index_to_name.get(getattr(self.cfg.audio, "device_index", 0), ""),
        }

    def get_config(self) -> Dict[str, Any]:
        # keep it explicit & stable for UI
        return {
            "audio": {
                "device_index": int(getattr(self.cfg.audio, "device_index", 0) or 0),
                "in_channel": getattr(self.cfg.audio, "in_channel", "mono") or "mono",
                "record_threshold": int(getattr(self.cfg.audio, "record_threshold", 75) or 0),
                "rectime": float(getattr(self.cfg.audio, "rectime", 0.1) or 0.1),
                "vox_silence_time": float(getattr(self.cfg.audio, "vox_silence_time", 2.0) or 2.0),
                "timeout_time_sec": int(getattr(self.cfg.audio, "timeout_time_sec", 120) or 120),
            },
            "general": {
                "save_audio": bool(getattr(self.cfg, "save_audio", False)),
                "mp3_bitrate": int(getattr(self.cfg, "mp3_bitrate", 32000) or 32000),
                "archive_dir": str(getattr(self.cfg, "archive_dir", "")),
            },
            "bcfy": {
                "api_key": getattr(self.cfg.bcfy, "api_key", "") or "",
                "system_id": getattr(self.cfg.bcfy, "system_id", "") or "",
                "slot_id": getattr(self.cfg.bcfy, "slot_id", "1") or "1",
                "freq_mhz": getattr(self.cfg.bcfy, "freq_mhz", "") or "",
            },
            "rdio": {
                "api_url": getattr(self.cfg.rdio, "api_url", "") or "",
                "api_key": getattr(self.cfg.rdio, "api_key", "") or "",
                "system": getattr(self.cfg.rdio, "system", "") or "",
                "talkgroup": getattr(self.cfg.rdio, "talkgroup", "") or "",
            },
            "icad_dispatch": {
                "api_url": getattr(self.cfg.icad_dispatch, "api_url", "") or "",
                "api_key": getattr(self.cfg.icad_dispatch, "api_key", "") or "",
                "system": getattr(self.cfg.icad_dispatch, "system", "") or "",
                "talkgroup": getattr(self.cfg.icad_dispatch, "talkgroup", "") or "",
            },
            "openmhz": {
                "api_key": getattr(self.cfg.openmhz, "api_key", "") or "",
                "short_name": getattr(self.cfg.openmhz, "short_name", "") or "",
                "tgid": getattr(self.cfg.openmhz, "tgid", "") or "",
            },
        }

    def patch_config(self, patch: Dict[str, Any]):
        """
        Patch the config using the same “shape” as get_config().
        If engine is running, we restart it when audio-related fields change.
        """
        restart_needed = False

        def _get(d: dict, *keys, default=None):
            cur = d
            for k in keys:
                if not isinstance(cur, dict) or k not in cur:
                    return default
                cur = cur[k]
            return cur

        audio = patch.get("audio") or {}
        general = patch.get("general") or {}
        bcfy = patch.get("bcfy") or {}
        rdio = patch.get("rdio") or {}
        icad = patch.get("icad_dispatch") or {}
        omhz = patch.get("openmhz") or {}

        # audio
        if "device_index" in audio:
            self.cfg.audio.device_index = int(audio["device_index"])
            restart_needed = True
        if "in_channel" in audio:
            self.cfg.audio.in_channel = str(audio["in_channel"] or "mono")
            restart_needed = True
        if "record_threshold" in audio:
            v = int(audio["record_threshold"] or 0)
            self.cfg.audio.record_threshold = v
            with self._lock:
                self.state.sql_threshold = max(0, min(100, v))
            restart_needed = restart_needed or False  # threshold doesn't require restart
        if "rectime" in audio:
            self.cfg.audio.rectime = float(audio["rectime"])
            restart_needed = True
        if "vox_silence_time" in audio:
            self.cfg.audio.vox_silence_time = float(audio["vox_silence_time"])
        if "timeout_time_sec" in audio:
            self.cfg.audio.timeout_time_sec = int(audio["timeout_time_sec"])

        # general
        if "save_audio" in general:
            self.cfg.save_audio = bool(general["save_audio"])
        if "mp3_bitrate" in general:
            self.cfg.mp3_bitrate = int(general["mp3_bitrate"])
        if "archive_dir" in general:
            self.cfg.archive_dir = str(general["archive_dir"] or "").strip()

        # bcfy
        if "api_key" in bcfy:
            self.cfg.bcfy.api_key = str(bcfy["api_key"] or "").strip()
        if "system_id" in bcfy:
            self.cfg.bcfy.system_id = str(bcfy["system_id"] or "").strip()
        if "slot_id" in bcfy:
            self.cfg.bcfy.slot_id = str(bcfy["slot_id"] or "1").strip() or "1"
        if "freq_mhz" in bcfy:
            self.cfg.bcfy.freq_mhz = str(bcfy["freq_mhz"] or "").strip()

        # rdio
        if "api_url" in rdio:
            self.cfg.rdio.api_url = str(rdio["api_url"] or "").strip()
        if "api_key" in rdio:
            self.cfg.rdio.api_key = str(rdio["api_key"] or "").strip()
        if "system" in rdio:
            self.cfg.rdio.system = str(rdio["system"] or "").strip()
        if "talkgroup" in rdio:
            self.cfg.rdio.talkgroup = str(rdio["talkgroup"] or "").strip()

        # icad
        if "api_url" in icad:
            self.cfg.icad_dispatch.api_url = str(icad["api_url"] or "").strip()
        if "api_key" in icad:
            self.cfg.icad_dispatch.api_key = str(icad["api_key"] or "").strip()
        if "system" in icad:
            self.cfg.icad_dispatch.system = str(icad["system"] or "").strip()
        if "talkgroup" in icad:
            self.cfg.icad_dispatch.talkgroup = str(icad["talkgroup"] or "").strip()

        # openmhz
        if "api_key" in omhz:
            self.cfg.openmhz.api_key = str(omhz["api_key"] or "").strip()
        if "short_name" in omhz:
            self.cfg.openmhz.short_name = str(omhz["short_name"] or "").strip()
        if "tgid" in omhz:
            self.cfg.openmhz.tgid = str(omhz["tgid"] or "").strip()

        save_config(self.cfg_path, self.cfg)

        # optional restart
        if restart_needed and self.is_running():
            self.stop()
            self.start()

        self.bus.emit("config", self.get_config())
        self.bus.emit("state", self.get_state())

    def is_running(self) -> bool:
        with self._lock:
            return bool(self.state.running)

    def start(self):
        if self.engine_thread and not self.engine_thread.is_alive():
            self.engine = None
            self.engine_thread = None
            with self._lock:
                self.state.running = False

        if self.engine_thread and self.engine_thread.is_alive():
            return

        hooks = UiHooks(
            set_status=self._set_status,
            set_status_color=self._set_status_color,
            set_bar=self._set_level,
        )

        self.engine = VoxCallEngine(self.cfg, version=self.version, hooks=hooks)

        def _run():
            with self._lock:
                self.state.running = True
                self.state.status_text = "RUNNING"
                self.state.updated_ts = time.time()
            self.bus.emit("state", self.get_state())

            try:
                self.engine.run_forever()
            except Exception as e:
                self._set_status(f"ERROR: {e}")
                self._set_status_color("red")
            finally:
                with self._lock:
                    self.state.running = False
                    self.state.led_rx = False
                    self.state.led_rec = False
                    self.state.status_text = "STOPPED"
                    self.state.updated_ts = time.time()
                self.bus.emit("state", self.get_state())

        self.engine_thread = threading.Thread(target=_run, daemon=True)
        self.engine_thread.start()

    def stop(self):
        eng = self.engine
        th = self.engine_thread

        if eng:
            try:
                eng.stop()
            except Exception:
                pass

        if th and th.is_alive():
            try:
                th.join(timeout=2.0)
            except Exception:
                pass

        self.engine = None
        self.engine_thread = None

        with self._lock:
            self.state.running = False
            self.state.led_rx = False
            self.state.led_rec = False
            self.state.status_text = "STOPPED"
            self.state.updated_ts = time.time()

        self.bus.emit("state", self.get_state())
