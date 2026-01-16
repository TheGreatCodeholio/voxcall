from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path
from typing import Optional, Tuple, Any

import ttkbootstrap as tb
from ttkbootstrap.constants import *
from tkinter import StringVar, IntVar, DoubleVar, BooleanVar, filedialog, PhotoImage

from voxcall.config import load_config, save_config
from voxcall.audio.devices import list_input_devices
from voxcall.engine import VoxCallEngine, UiHooks
from voxcall.ui.widgets import validate_number
from voxcall.paths import resource_path

log = logging.getLogger(__name__)

def _validate_float(P: str) -> bool:
    """Allow empty, digits, or one decimal point."""
    if P == "":
        return True
    try:
        float(P)
        return True
    except ValueError:
        return False


class VoxCallGui:
    """
    Dark-theme focused UI polish:
      - cleaner layout (single padded surface + card-like groups)
      - consistent dark bootstyles
      - button styling that reads well on dark themes
      - robust level normalization (dB / 0..1 / 0..100)
    """

    DB_MIN = -80.0
    DB_MAX = 0.0

    def __init__(self, cfg_path: Path, version: str, theme: str = "darkly"):
        self.cfg_path = cfg_path
        self.version = version
        self.cfg = load_config(cfg_path)

        self._set_windows_appid()
        self.root = tb.Window(themename=theme)
        self.root.title(f"VoxCall • {version}")
        self._apply_window_icon()
        self.root.minsize(900, 680)
        #self.root.geometry("1020x760")

        if getattr(self.cfg, "start_minimized", False):
            self.root.iconify()

        # devices
        self.input_devices, self.name_to_index, self.index_to_name = list_input_devices()

        # engine state
        self.engine: VoxCallEngine | None = None
        self.engine_thread: threading.Thread | None = None
        self._autosave_after_id = None

        # meter state
        self._sig_bootstyle: Optional[str] = None
        self._squelch_open = False
        self._sql_hyst = 3

        # tk vars
        self.status_text = StringVar(value="STANDBY")
        self.running = BooleanVar(value=False)

        self.level_value = IntVar(value=0)        # 0..100
        self.level_text = StringVar(value="000")  # percent
        self.db_text = StringVar(value="")        # "-46.8 dB"

        self.var_threshold = IntVar(value=max(int(getattr(self.cfg.audio, "record_threshold", 75)), 0))
        self.sql_text = StringVar(value=f"{int(self.var_threshold.get()):03d}")

        # Audio
        self.var_device = StringVar(
            value=self.index_to_name.get(
                getattr(self.cfg.audio, "device_index", 0),
                self.input_devices[0] if self.input_devices else "",
            )
        )
        self.var_channel = StringVar(value=getattr(self.cfg.audio, "in_channel", "mono"))
        self.var_rectime = DoubleVar(value=float(getattr(self.cfg.audio, "rectime", 0.1)))
        self.var_silence = DoubleVar(value=float(getattr(self.cfg.audio, "vox_silence_time", 2.0)))
        self.var_timeout = IntVar(value=int(getattr(self.cfg.audio, "timeout_time_sec", 120)))

        # General
        self.var_save_audio = BooleanVar(value=bool(getattr(self.cfg, "save_audio", False)))
        self.var_bitrate = IntVar(value=int(getattr(self.cfg, "mp3_bitrate", 32000)))
        default_archive = str(Path("audiosave").resolve())
        self.var_archive_dir = StringVar(value=str(getattr(self.cfg, "archive_dir", default_archive)))

        # Broadcastify
        self.var_bcfy_key = StringVar(value=getattr(self.cfg.bcfy, "api_key", ""))
        self.var_bcfy_sysid = StringVar(value=getattr(self.cfg.bcfy, "system_id", ""))
        self.var_bcfy_slot = StringVar(value=getattr(self.cfg.bcfy, "slot_id", "1"))
        self.var_bcfy_freq = StringVar(value=getattr(self.cfg.bcfy, "freq_mhz", ""))

        # rdio-scanner
        self.var_rdio_url = StringVar(value=getattr(self.cfg.rdio, "api_url", ""))
        self.var_rdio_key = StringVar(value=getattr(self.cfg.rdio, "api_key", ""))
        self.var_rdio_sys = StringVar(value=getattr(self.cfg.rdio, "system", ""))
        self.var_rdio_tg = StringVar(value=getattr(self.cfg.rdio, "talkgroup", ""))

        # iCad Dispatch
        icad = getattr(self.cfg, "icad_dispatch", None)
        self.var_icad_url = StringVar(value=getattr(icad, "api_url", ""))
        self.var_icad_key = StringVar(value=getattr(icad, "api_key", ""))
        self.var_icad_sys = StringVar(value=getattr(icad, "system", ""))
        self.var_icad_tg  = StringVar(value=getattr(icad, "talkgroup", ""))

        # OpenMHz
        self.var_omhz_key = StringVar(value=getattr(self.cfg.openmhz, "api_key", ""))
        self.var_omhz_short = StringVar(value=getattr(self.cfg.openmhz, "short_name", ""))
        self.var_omhz_tgid = StringVar(value=getattr(self.cfg.openmhz, "tgid", ""))

        self._build()
        self._apply_initial_geometry()
        self._bind_autosave()
        self._start_engine()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------- UI ----------------
    def _apply_window_icon(self) -> None:
        ico = resource_path("resources/voxcall.ico")
        png = resource_path("resources/voxcall.png")

        ico_s = str(ico.resolve()) if ico.exists() else ""
        png_s = str(png.resolve()) if png.exists() else ""

        # --- Linux: dock matching (needs a .desktop with StartupWMClass)
        if sys.platform.startswith("linux"):
            try:
                self.root.wm_class("voxcall", "VoxCall")
            except Exception as e:
                log.warning("wm_class failed: %s", e)

        # --- Windows: titlebar icon (top-left) prefers .ico
        if sys.platform.startswith("win") and ico_s:
            try:
                self.root.iconbitmap(default=ico_s)
            except Exception as e:
                log.warning("iconbitmap failed (%s): %s", ico_s, e)

        # --- Cross-platform: provide multiple sizes to wm iconphoto
        if png_s:
            try:
                from PIL import Image, ImageTk

                base = Image.open(png_s).convert("RGBA")
                sizes = (16, 20, 24, 32, 40, 48, 64, 128, 256)

                self._icon_imgs = []
                for s in sizes:
                    im = base.resize((s, s), Image.LANCZOS)
                    self._icon_imgs.append(ImageTk.PhotoImage(im))

                self.root.iconphoto(True, *self._icon_imgs)
            except Exception as e:
                log.warning("iconphoto failed (%s): %s", png_s, e)

        # --- Some Tk/Windows combos apply after the window is realized; nudge once
        if sys.platform.startswith("win") and ico_s:
            self.root.after_idle(lambda: self.root.iconbitmap(default=ico_s))

    def _set_windows_appid(self):
        if sys.platform.startswith("win") and not getattr(sys, "frozen", False):
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    "ThinlineDynamicSolutions.VoxCall"
                )
            except Exception:
                pass

    def _build(self):
        # One “surface” frame so everything feels like one window, not nested boxes.
        surface = tb.Frame(self.root, padding=18, bootstyle="dark")
        surface.pack(fill=BOTH, expand=True)

        # Top card: status + meters
        top_card = tb.Labelframe(
            surface,
            text=" Live ",
            padding=(16, 14),
            bootstyle="light",
        )
        top_card.pack(fill=X)

        self._build_header(top_card)
        self._build_meters(top_card)

        # Main card: tabs
        main_card = tb.Labelframe(
            surface,
            text=" Settings ",
            padding=(12, 10),
            bootstyle="light",
        )
        main_card.pack(fill=BOTH, expand=True, pady=(14, 0))

        nb = tb.Notebook(main_card, bootstyle="secondary")
        nb.pack(fill=BOTH, expand=True)

        self.tab_general = tb.Frame(nb, padding=14)
        self.tab_audio = tb.Frame(nb, padding=14)
        self.tab_bcfy = tb.Frame(nb, padding=14)
        self.tab_rdio = tb.Frame(nb, padding=14)
        self.tab_icad = tb.Frame(nb, padding=14)
        self.tab_omhz = tb.Frame(nb, padding=14)

        nb.add(self.tab_general, text="General")
        nb.add(self.tab_audio, text="Audio")
        nb.add(self.tab_bcfy, text="Broadcastify")
        nb.add(self.tab_rdio, text="RDIO")
        nb.add(self.tab_icad, text="iCad Dispatch")
        nb.add(self.tab_omhz, text="OpenMHz")

        self._build_tab_general()
        self._build_tab_audio()
        self._build_tab_bcfy()
        self._build_tab_rdio()
        self._build_tab_icad()
        self._build_tab_omhz()

        # Footer card: controls
        footer = tb.Frame(surface, padding=(0, 14, 0, 0), bootstyle="dark")
        footer.pack(side=BOTTOM, fill=X)
        self._build_footer(footer)

        self._update_threshold_ui()
        self._update_buttons()

    def _apply_initial_geometry(self, min_w: int = 900, min_h: int = 680):
        # Must be called AFTER _build() so req size is accurate
        self.root.update_idletasks()

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()

        # What Tk actually wants for your packed widgets
        req_w = max(self.root.winfo_reqwidth(), min_w)
        req_h = max(self.root.winfo_reqheight(), min_h)

        # Leave room for title bar + taskbar / docks
        pad_w = 80
        pad_h = 140

        w = min(req_w, max(640, sw - pad_w))
        h = min(req_h, max(520, sh - pad_h))

        # Don’t allow resizing smaller than what fits on THIS screen
        self.root.minsize(min(min_w, w), min(min_h, h))

        # Center-ish
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 3)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _build_header(self, parent: tb.Frame):
        row = tb.Frame(parent)
        row.pack(fill=X)

        left = tb.Frame(row)
        left.pack(side=LEFT)

        tb.Label(
            left,
            text="VOXCALL",
            font=("TkFixedFont", 16, "bold"),
            bootstyle="light",  # crisp in dark themes
        ).pack(anchor=W)

        tb.Label(
            left,
            text=f"v{self.version}",
            bootstyle="light",
        ).pack(anchor=W, pady=(2, 0))

        # center “LCD”
        lcd = tb.Frame(row, bootstyle="dark", padding=(12, 8))
        lcd.pack(side=LEFT, fill=X, expand=True, padx=(18, 18))

        self.lcd_label = tb.Label(
            lcd,
            textvariable=self.status_text,
            font=("TkFixedFont", 14, "bold"),
            bootstyle="light",
            anchor=W,
        )
        self.lcd_label.pack(fill=X)

        # right LEDs
        right = tb.Frame(row)
        right.pack(side=RIGHT)

        self.led_rx = tb.Label(
            right,
            text=" RX ",
            font=("TkFixedFont", 11, "bold"),
            bootstyle="light",
            padding=(8, 4),
        )
        self.led_rec = tb.Label(
            right,
            text=" REC ",
            font=("TkFixedFont", 11, "bold"),
            bootstyle="light",
            padding=(8, 4),
        )
        self.led_rx.pack(anchor=E)
        self.led_rec.pack(anchor=E, pady=(8, 0))

    def _build_meters(self, parent: tb.Frame):
        block = tb.Frame(parent, padding=(0, 12, 0, 0))
        block.pack(fill=X)

        # SIG row
        sig_row = tb.Frame(block)
        sig_row.pack(fill=X)

        tb.Label(
            sig_row,
            text="SIG",
            font=("TkFixedFont", 12, "bold"),
            bootstyle="light",
            padding=(10, 6),
        ).pack(side=LEFT)

        # SIG readout on right (match SQL styling)
        sig_read = tb.Label(
            sig_row,
            textvariable=self.level_text,   # "000"
            width=4,
            anchor=E,
            bootstyle="light",
        )
        sig_read.pack(side=RIGHT, padx=(10, 0))


        self.sig_wrap = tb.Frame(sig_row)
        self.sig_wrap.pack(side=LEFT, fill=X, expand=True, padx=(12, 12))

        # Start neutral; flips to success when SQL opens.
        self.sig_bar = tb.Progressbar(
            self.sig_wrap,
            maximum=100,
            mode="determinate",
            variable=self.level_value,
            bootstyle="info",
        )
        self.sig_bar.pack(fill=X, ipady=8)

        self.thr_marker = tb.Frame(self.sig_wrap, width=2, bootstyle="light")
        self.thr_marker.place(relx=0.0, rely=0.5, anchor="center", relheight=1.55)

        # SQL row
        sql_row = tb.Frame(block)
        sql_row.pack(fill=X, pady=(10, 0))

        tb.Label(
            sql_row,
            text="SQL",
            font=("TkFixedFont", 12, "bold"),
            bootstyle="light",
            padding=(10, 6),
        ).pack(side=LEFT)

        sql_read = tb.Label(sql_row, textvariable=self.sql_text, width=4, anchor=E, bootstyle="light")
        sql_read.pack(side=RIGHT, padx=(10, 0))

        sql_mid = tb.Frame(sql_row)
        sql_mid.pack(side=LEFT, fill=X, expand=True, padx=(12, 12))

        # Scale in dark theme: "info" reads nicely and doesn't scream.
        self.threshold_scale = tb.Scale(
            sql_mid,
            from_=0,
            to=100,
            variable=self.var_threshold,
            bootstyle="info",
        )
        self.threshold_scale.pack(fill=X)

        # helper text under meters (small, subtle)
        tb.Label(
            parent,
            text="SIG = live input level • SQL = recording threshold",
            bootstyle="light",
        ).pack(anchor=W, pady=(8, 0))

    def _build_footer(self, parent: tb.Frame):
        # A single line that feels “built-in” to the window.
        bar = tb.Frame(parent)
        bar.pack(fill=X)

        # Left controls (keep Start/Stop where they are)
        left = tb.Frame(bar)
        left.pack(side=LEFT)

        self.btn_start = tb.Button(
            left,
            text="Start",
            command=self._start_engine,
            bootstyle="success",
            width=10,
        )
        self.btn_stop = tb.Button(
            left,
            text="Stop",
            command=self._stop_engine,
            bootstyle="danger",
            width=10,
        )
        self.btn_start.pack(side=LEFT)
        self.btn_stop.pack(side=LEFT, padx=(10, 0))

        # Middle separator
        tb.Separator(bar, orient=VERTICAL).pack(side=LEFT, fill=Y, padx=14, pady=2)

        # Save: neutral but visible on dark background
        self.btn_save = tb.Button(
            bar,
            text="Save",
            command=self._save_only,
            bootstyle="success-outline",  # crisp, dark-friendly
            width=10,
        )
        self.btn_save.pack(side=LEFT)

        # Right exit: make it obvious and consistent
        self.btn_exit = tb.Button(
            bar,
            text="Exit",
            command=self._exit,
            bootstyle="light-outline",
            width=10,
        )
        self.btn_exit.pack(side=RIGHT)

    # ---------------- Tabs ----------------

    def _build_tab_general(self):
        f = self.tab_general

        lf = tb.Labelframe(f, text="Behavior", padding=14, bootstyle="light")
        lf.pack(fill=X)

        tb.Checkbutton(
            lf,
            text="Archive audio files",
            variable=self.var_save_audio,
            bootstyle="round-toggle",
        ).pack(anchor=W, pady=(0, 10))

        row = tb.Frame(lf)
        row.pack(fill=X, pady=(0, 10))
        tb.Label(row, text="Archive folder", width=18, bootstyle="light").pack(side=LEFT)
        tb.Entry(row, textvariable=self.var_archive_dir).pack(side=LEFT, fill=X, expand=True, padx=(0, 10))

        def _browse():
            d = filedialog.askdirectory(title="Select archive folder")
            if d:
                self.var_archive_dir.set(d)

        tb.Button(row, text="Browse…", command=_browse, bootstyle="info").pack(side=LEFT)

        row = tb.Frame(lf)
        row.pack(fill=X)
        tb.Label(row, text="MP3 bitrate (bps)", width=18, bootstyle="light").pack(side=LEFT)
        tb.Entry(row, textvariable=self.var_bitrate, width=12).pack(side=LEFT)
        tb.Label(row, text="(example: 32000)", bootstyle="light").pack(side=LEFT, padx=(10, 0))

    def _build_tab_audio(self):
        f = self.tab_audio

        lf = tb.Labelframe(f, text="Input", padding=14, bootstyle="light")
        lf.pack(fill=X, pady=(0, 12))

        row = tb.Frame(lf)
        row.pack(fill=X, pady=6)
        tb.Label(row, text="Device", width=18, bootstyle="light").pack(side=LEFT)
        self.cmb_device = tb.Combobox(row, textvariable=self.var_device, values=self.input_devices, state="readonly")
        self.cmb_device.pack(side=LEFT, fill=X, expand=True)
        self.cmb_device.bind("<<ComboboxSelected>>", lambda _e: self._restart_engine_if_running())

        row = tb.Frame(lf)
        row.pack(fill=X, pady=6)
        tb.Label(row, text="Channel", width=18, bootstyle="light").pack(side=LEFT)
        self.cmb_channel = tb.Combobox(
            row,
            textvariable=self.var_channel,
            values=["mono", "left", "right"],
            state="readonly",
            width=12,
        )
        self.cmb_channel.pack(side=LEFT)
        self.cmb_channel.bind("<<ComboboxSelected>>", lambda _e: self._restart_engine_if_running())

        adv = tb.Labelframe(f, text="Detection Tuning", padding=14, bootstyle="light")
        adv.pack(fill=X)

        vcmd_float = (f.register(_validate_float), "%P")
        vcmd_int = (f.register(validate_number), "%P")

        row = tb.Frame(adv)
        row.pack(fill=X, pady=6)
        tb.Label(row, text="rectime (seconds)", width=18, bootstyle="light").pack(side=LEFT)
        tb.Entry(row, textvariable=self.var_rectime, width=12, validate="key", validatecommand=vcmd_float).pack(side=LEFT)
        tb.Label(row, text="How often we sample audio", bootstyle="light").pack(side=LEFT, padx=(10, 0))

        row = tb.Frame(adv)
        row.pack(fill=X, pady=6)
        tb.Label(row, text="Silence stop (sec)", width=18, bootstyle="light").pack(side=LEFT)
        tb.Entry(row, textvariable=self.var_silence, width=12, validate="key", validatecommand=vcmd_float).pack(side=LEFT)

        row = tb.Frame(adv)
        row.pack(fill=X, pady=6)
        tb.Label(row, text="Timeout (sec)", width=18, bootstyle="light").pack(side=LEFT)
        tb.Entry(row, textvariable=self.var_timeout, width=12, validate="key", validatecommand=vcmd_int).pack(side=LEFT)


    def _build_tab_bcfy(self):
        f = self.tab_bcfy
        lf = tb.Labelframe(f, text="Broadcastify", padding=14, bootstyle="light")
        lf.pack(fill=X)

        vcmd = (f.register(validate_number), "%P")

        self._kv_row(lf, "API Key", self.var_bcfy_key, width=60)
        self._kv_row(lf, "System ID", self.var_bcfy_sysid, width=20, validate="key", vcmd=vcmd)
        self._kv_row(lf, "Slot ID", self.var_bcfy_slot, width=10, validate="key", vcmd=vcmd)
        self._kv_row(lf, "Freq (MHz)", self.var_bcfy_freq, width=12)

        tb.Label(f, text="Blank API Key = Broadcastify uploads skipped.", bootstyle="light").pack(anchor=W, pady=(10, 0))

    def _build_tab_rdio(self):
        f = self.tab_rdio
        lf = tb.Labelframe(f, text="rdio-scanner", padding=14, bootstyle="light")
        lf.pack(fill=X)

        vcmd = (f.register(validate_number), "%P")

        self._kv_row(lf, "API URL", self.var_rdio_url, width=60)
        self._kv_row(lf, "API Key", self.var_rdio_key, width=60)
        self._kv_row(lf, "System ID", self.var_rdio_sys, width=20, validate="key", vcmd=vcmd)
        self._kv_row(lf, "Talkgroup", self.var_rdio_tg, width=20, validate="key", vcmd=vcmd)

        tb.Label(f, text="If any field is blank, rdio-scanner upload is skipped.", bootstyle="light").pack(anchor=W, pady=(10, 0))

    def _build_tab_icad(self):
        f = self.tab_icad
        lf = tb.Labelframe(f, text="iCad Dispatch", padding=14, bootstyle="light")
        lf.pack(fill=X)

        vcmd = (f.register(validate_number), "%P")

        # same inputs/layout as RDIO
        self._kv_row(lf, "API URL", self.var_icad_url, width=60)
        self._kv_row(lf, "API Key", self.var_icad_key, width=60)
        self._kv_row(lf, "System ID", self.var_icad_sys, width=20, validate="key", vcmd=vcmd)
        self._kv_row(lf, "Talkgroup", self.var_icad_tg, width=20, validate="key", vcmd=vcmd)

        tb.Label(
            f,
            text="If any field is blank, iCad Dispatch upload is skipped.",
            bootstyle="light",
        ).pack(anchor=W, pady=(10, 0))

    def _build_tab_omhz(self):
        f = self.tab_omhz
        lf = tb.Labelframe(f, text="OpenMHz", padding=14, bootstyle="light")
        lf.pack(fill=X)

        vcmd = (f.register(validate_number), "%P")

        self._kv_row(lf, "API Key", self.var_omhz_key, width=60)
        self._kv_row(lf, "Short Name", self.var_omhz_short, width=20)
        self._kv_row(lf, "TGID", self.var_omhz_tgid, width=20, validate="key", vcmd=vcmd)

        tb.Label(f, text="OpenMHz uses Broadcastify Freq for upload metadata.", bootstyle="light").pack(anchor=W, pady=(10, 0))

    def _kv_row(self, parent, label: str, var, width: int = 40, validate: str = "", vcmd=None):
        row = tb.Frame(parent)
        row.pack(fill=X, pady=6)
        tb.Label(row, text=label, width=18, bootstyle="light").pack(side=LEFT)
        e = tb.Entry(row, textvariable=var, width=width)
        if validate and vcmd:
            e.configure(validate=validate, validatecommand=vcmd)
        e.pack(side=LEFT, fill=X, expand=True)

    # ---------------- autosave ----------------

    def _bind_autosave(self):
        vars_to_watch = [
            self.var_device, self.var_channel,
            self.var_threshold,
            self.var_rectime, self.var_silence, self.var_timeout,
            self.var_save_audio, self.var_bitrate, self.var_archive_dir,
            self.var_bcfy_key, self.var_bcfy_sysid, self.var_bcfy_slot, self.var_bcfy_freq,
            self.var_rdio_url, self.var_rdio_key, self.var_rdio_sys, self.var_rdio_tg,
            self.var_icad_url, self.var_icad_key, self.var_icad_sys, self.var_icad_tg,
            self.var_omhz_key, self.var_omhz_short, self.var_omhz_tgid,
        ]

        for v in vars_to_watch:
            try:
                v.trace_add("write", lambda *_: self._schedule_autosave())
            except Exception:
                pass

        self.var_threshold.trace_add("write", lambda *_: self._update_threshold_ui())

    def _schedule_autosave(self, delay_ms: int = 500):
        if self._autosave_after_id is not None:
            try:
                self.root.after_cancel(self._autosave_after_id)
            except Exception:
                pass
        self._autosave_after_id = self.root.after(delay_ms, self._autosave_now)

    def _autosave_now(self):
        self._autosave_after_id = None
        self._save_only(silent=True)

    # ---------------- meter helpers ----------------

    def _update_threshold_ui(self):
        thr = int(self.var_threshold.get() or 0)
        thr = max(0, min(100, thr))
        self.sql_text.set(f"{thr:03d}")
        try:
            self.thr_marker.place_configure(relx=thr / 100.0)
        except Exception:
            pass

    def _set_led(self, led: tb.Label, on: bool, on_style="success", off_style="light"):
        try:
            led.configure(bootstyle=(on_style if on else off_style))
        except Exception:
            pass

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

    # ---------------- Thread-safe UI hooks ----------------

    def _ui(self, fn, *a, **kw):
        self.root.after(0, lambda: fn(*a, **kw))

    def _set_status(self, text: str):
        self.status_text.set((text or "").strip()[:64])

    def _set_status_color(self, color: str):
        if color == "red":
            self._set_led(self.led_rec, True, on_style="danger")
        elif color == "green":
            self._set_led(self.led_rec, True, on_style="danger")
        else:
            self._set_led(self.led_rec, False)

    def _set_level(self, v: Any):
        pct, db = self._normalize_level(v)

        self.level_value.set(pct)
        self.level_text.set(f"{pct:03d}")

        self.db_text.set("" if db is None else f"{db:5.1f} dB")

        thr = max(0, min(100, int(self.var_threshold.get() or 0)))

        if thr == 0:
            open_now = True
        else:
            if self._squelch_open:
                open_now = pct >= max(0, thr - self._sql_hyst)
            else:
                open_now = pct >= min(100, thr + self._sql_hyst)

        self._set_led(self.led_rx, open_now, on_style="success", off_style="light")

        if open_now != self._squelch_open:
            self._squelch_open = open_now
            style = "success" if open_now else "info"
            if style != self._sig_bootstyle:
                self._sig_bootstyle = style
                try:
                    self.sig_bar.configure(bootstyle=style)
                except Exception:
                    pass

    # ---------------- Engine control ----------------

    def _start_engine(self):
        if self.engine_thread and not self.engine_thread.is_alive():
            self.engine = None
            self.engine_thread = None
            self.running.set(False)

        if self.engine and self.running.get():
            return

        self._sync_cfg_from_ui()

        hooks = UiHooks(
            set_status=lambda s: self._ui(self._set_status, s),
            set_status_color=lambda c: self._ui(self._set_status_color, c),
            set_bar=lambda v: self._ui(self._set_level, v),
        )

        self.engine = VoxCallEngine(self.cfg, version=self.version, hooks=hooks)

        def _engine_entry():
            try:
                self.engine.run_forever()
            except Exception as e:
                self._ui(self._set_status, f"ERROR: {e}")
                self._ui(self._set_status_color, "red")
            finally:
                self._ui(self.running.set, False)
                self._ui(self._update_buttons)

        self.engine_thread = threading.Thread(target=_engine_entry, daemon=True)
        self.engine_thread.start()

        self.running.set(True)
        self._update_buttons()
        self._set_led(self.led_rec, False)
        self._set_status("RUNNING")

    def _stop_engine(self):
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

        self.running.set(False)
        self._update_buttons()
        self._set_led(self.led_rx, False)
        self._set_status("STOPPED")

    def _restart_engine_if_running(self):
        if self.running.get():
            self._stop_engine()
            self._start_engine()

    def _update_buttons(self):
        is_running = self.running.get()
        self.btn_start.configure(state=("disabled" if is_running else "normal"))
        self.btn_stop.configure(state=("normal" if is_running else "disabled"))

    # ---------------- Config sync + actions ----------------

    def _sync_cfg_from_ui(self):
        # ---- small helpers ----
        def _get_str(var, default: str = "") -> str:
            try:
                v = var.get()
            except Exception:
                return default
            return (v or default).strip()

        def _get_int(var, default: int = 0, *, min_v: int | None = None, max_v: int | None = None) -> int:
            try:
                v = int(var.get())
            except Exception:
                v = default
            if min_v is not None:
                v = max(min_v, v)
            if max_v is not None:
                v = min(max_v, v)
            return v

        def _get_float(var, default: float = 0.0, *, min_v: float | None = None, max_v: float | None = None) -> float:
            try:
                v = float(var.get())
            except Exception:
                v = default
            if min_v is not None:
                v = max(min_v, v)
            if max_v is not None:
                v = min(max_v, v)
            return v

        def _ensure_section(obj, attr: str):
            """
            Ensure obj.<attr> exists. If config model forbids unknown fields,
            this may fail; we handle that gracefully.
            """
            sec = getattr(obj, attr, None)
            if sec is not None:
                return sec
            try:
                from types import SimpleNamespace
                sec = SimpleNamespace()
                setattr(obj, attr, sec)
                return sec
            except Exception:
                return None

        # ---- audio ----
        audio = _ensure_section(self.cfg, "audio")
        if audio is not None:
            # device index: keep previous if lookup fails
            try:
                audio.device_index = self.name_to_index.get(
                    _get_str(self.var_device),
                    getattr(audio, "device_index", 0),
                )
            except Exception:
                pass

            try:
                audio.in_channel = _get_str(self.var_channel, "mono")
            except Exception:
                pass

            try:
                audio.record_threshold = _get_int(self.var_threshold, 0, min_v=0, max_v=100)
            except Exception:
                pass

            # tune: keep sane bounds
            try:
                audio.rectime = _get_float(self.var_rectime, 0.1, min_v=0.01, max_v=10.0)
            except Exception:
                pass
            try:
                audio.vox_silence_time = _get_float(self.var_silence, 2.0, min_v=0.0, max_v=60.0)
            except Exception:
                pass
            try:
                audio.timeout_time_sec = _get_int(self.var_timeout, 120, min_v=1, max_v=24 * 3600)
            except Exception:
                pass

        # ---- general ----
        try:
            self.cfg.save_audio = bool(self.var_save_audio.get())
        except Exception:
            pass

        try:
            # 8k..320k typical range, adjust if you want
            self.cfg.mp3_bitrate = _get_int(self.var_bitrate, 32000, min_v=8000, max_v=320000)
        except Exception:
            pass

        # only set if your config supports it
        archive_dir = _get_str(self.var_archive_dir, "")
        if archive_dir:
            try:
                setattr(self.cfg, "archive_dir", archive_dir)
            except Exception:
                pass

        # ---- broadcastify ----
        bcfy = _ensure_section(self.cfg, "bcfy")
        if bcfy is not None:
            try:
                bcfy.api_key = _get_str(self.var_bcfy_key)
                bcfy.system_id = _get_str(self.var_bcfy_sysid)
                bcfy.slot_id = _get_str(self.var_bcfy_slot, "1") or "1"
                bcfy.freq_mhz = _get_str(self.var_bcfy_freq)
            except Exception:
                pass

        # ---- rdio ----
        rdio = _ensure_section(self.cfg, "rdio")
        if rdio is not None:
            try:
                rdio.api_url = _get_str(self.var_rdio_url)
                rdio.api_key = _get_str(self.var_rdio_key)
                rdio.system = _get_str(self.var_rdio_sys)
                rdio.talkgroup = _get_str(self.var_rdio_tg)
            except Exception:
                pass

        # ---- icad dispatch ----
        icad = _ensure_section(self.cfg, "icad_dispatch")
        if icad is not None:
            try:
                icad.api_url = _get_str(self.var_icad_url)
                icad.api_key = _get_str(self.var_icad_key)
                icad.system = _get_str(self.var_icad_sys)
                icad.talkgroup = _get_str(self.var_icad_tg)
            except Exception:
                pass

        # ---- openmhz ----
        openmhz = _ensure_section(self.cfg, "openmhz")
        if openmhz is not None:
            try:
                openmhz.api_key = _get_str(self.var_omhz_key)
                openmhz.short_name = _get_str(self.var_omhz_short)
                openmhz.tgid = _get_str(self.var_omhz_tgid)
            except Exception:
                pass

    def _save_only(self, silent: bool = False):
        self._sync_cfg_from_ui()
        save_config(self.cfg_path, self.cfg)
        if not silent:
            self._set_status("SAVED")

    def _exit(self):
        try:
            self._save_only(silent=True)
        except Exception:
            pass
        self._stop_engine()
        self.root.destroy()

    def _on_close(self):
        self._exit()

    def run(self):
        self.root.mainloop()
