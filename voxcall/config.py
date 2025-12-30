from __future__ import annotations

import logging
from dataclasses import dataclass, field
from configparser import ConfigParser
from pathlib import Path
import os

log = logging.getLogger(__name__)

@dataclass
class AudioCfg:
    device_index: int = 0
    record_threshold: int = 75
    vox_silence_time: float = 2.0
    in_channel: str = "mono"   # mono/left/right
    rectime: float = 0.1
    timeout_time_sec: int = 120

    prefer_rate: int = 0                 # 0 => auto (device default / probe)
    monitor_output: bool = False         # original had output=True; Linux often prefers False
    output_device_index: int = -1        # -1 => default output device

@dataclass
class BroadcastifyCfg:
    api_key: str = ""
    system_id: str = ""
    slot_id: str = "1"
    freq_mhz: str = ""  # string for UI convenience

@dataclass
class RdioCfg:
    api_url: str = ""
    api_key: str = ""
    system: str = ""
    talkgroup: str = ""

@dataclass
class OpenMHzCfg:
    api_key: str = ""
    short_name: str = ""
    tgid: str = ""

@dataclass
class AppCfg:
    mp3_bitrate: int = 32000
    start_minimized: bool = False
    save_audio: bool = False

    # NEW: archive destination when save_audio is enabled
    archive_dir: str = "audiosave"

    audio: AudioCfg = field(default_factory=AudioCfg)
    bcfy: BroadcastifyCfg = field(default_factory=BroadcastifyCfg)
    rdio: RdioCfg = field(default_factory=RdioCfg)
    openmhz: OpenMHzCfg = field(default_factory=OpenMHzCfg)

def load_config(path: Path) -> AppCfg:
    cfg = AppCfg()
    p = ConfigParser()
    p.read(path)
    s = "Section1"

    get  = lambda k, d="": p.get(s, k, fallback=d)
    geti = lambda k, d=0: p.getint(s, k, fallback=d)
    getf = lambda k, d=0.0: p.getfloat(s, k, fallback=d)

    # app
    cfg.mp3_bitrate = geti("mp3_bitrate", cfg.mp3_bitrate)
    cfg.start_minimized = bool(geti("start_minimized", 1 if cfg.start_minimized else 0))
    cfg.save_audio = bool(geti("saveaudio", 1 if cfg.save_audio else 0))
    cfg.archive_dir = get("archive_dir", cfg.archive_dir) or "audiosave"

    # audio
    cfg.audio.device_index = geti("audio_dev_index", cfg.audio.device_index)
    cfg.audio.record_threshold = geti("record_threshold", cfg.audio.record_threshold)
    cfg.audio.vox_silence_time = getf("vox_silence_time", cfg.audio.vox_silence_time)
    cfg.audio.in_channel = get("in_channel", cfg.audio.in_channel)

    cfg.audio.rectime = getf("rectime", cfg.audio.rectime)
    cfg.audio.timeout_time_sec = geti("timeout_time_sec", cfg.audio.timeout_time_sec)

    cfg.audio.prefer_rate = geti("prefer_rate", cfg.audio.prefer_rate)
    cfg.audio.monitor_output = bool(geti("monitor_output", 1 if cfg.audio.monitor_output else 0))
    cfg.audio.output_device_index = geti("output_device_index", cfg.audio.output_device_index)

    # bcfy
    cfg.bcfy.system_id = get("BCFY_SystemId", cfg.bcfy.system_id)
    cfg.bcfy.slot_id   = get("BCFY_SlotId", cfg.bcfy.slot_id)
    cfg.bcfy.freq_mhz  = get("RadioFreq", cfg.bcfy.freq_mhz)
    cfg.bcfy.api_key   = get("BCFY_APIkey", cfg.bcfy.api_key)

    # rdio
    cfg.rdio.api_key = get("RDIO_APIkey", cfg.rdio.api_key)
    cfg.rdio.api_url = get("RDIO_APIurl", cfg.rdio.api_url)
    cfg.rdio.system  = get("RDIO_system", cfg.rdio.system)
    cfg.rdio.talkgroup = get("RDIO_tg", cfg.rdio.talkgroup)

    # openmhz
    cfg.openmhz.api_key = get("openmhz_api_key", cfg.openmhz.api_key)
    cfg.openmhz.short_name = get("openmhz_short_name", cfg.openmhz.short_name)
    cfg.openmhz.tgid = get("openmhz_tgid", cfg.openmhz.tgid)

    return cfg

def save_config(path: Path, cfg: AppCfg) -> None:
    path = Path(os.path.expanduser(str(path))).resolve()
    log.warning("save_config() writing to: %s (cwd=%s)", path, Path.cwd())
    p = ConfigParser()
    p.read(path)
    if "Section1" not in p.sections():
        p.add_section("Section1")
    s = "Section1"

    # app
    p.set(s, "mp3_bitrate", str(cfg.mp3_bitrate))
    p.set(s, "start_minimized", "1" if cfg.start_minimized else "0")
    p.set(s, "saveaudio", "1" if cfg.save_audio else "0")
    p.set(s, "archive_dir", (cfg.archive_dir or "audiosave"))

    # audio
    p.set(s, "audio_dev_index", str(cfg.audio.device_index))
    p.set(s, "record_threshold", str(cfg.audio.record_threshold))
    p.set(s, "vox_silence_time", str(cfg.audio.vox_silence_time))
    p.set(s, "in_channel", cfg.audio.in_channel)

    p.set(s, "rectime", str(cfg.audio.rectime))
    p.set(s, "timeout_time_sec", str(cfg.audio.timeout_time_sec))

    p.set(s, "prefer_rate", str(cfg.audio.prefer_rate))
    p.set(s, "monitor_output", "1" if cfg.audio.monitor_output else "0")
    p.set(s, "output_device_index", str(cfg.audio.output_device_index))

    # bcfy
    p.set(s, "BCFY_SystemId", cfg.bcfy.system_id)
    p.set(s, "RadioFreq", cfg.bcfy.freq_mhz)
    p.set(s, "BCFY_APIkey", cfg.bcfy.api_key)
    p.set(s, "BCFY_SlotId", cfg.bcfy.slot_id)

    # rdio
    p.set(s, "RDIO_APIkey", cfg.rdio.api_key)
    p.set(s, "RDIO_APIurl", cfg.rdio.api_url)
    p.set(s, "RDIO_system", cfg.rdio.system)
    p.set(s, "RDIO_tg", cfg.rdio.talkgroup)

    # openmhz
    p.set(s, "openmhz_api_key", cfg.openmhz.api_key)
    p.set(s, "openmhz_short_name", cfg.openmhz.short_name)
    p.set(s, "openmhz_tgid", cfg.openmhz.tgid)

    # safer write (atomic)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        p.write(f)
    os.replace(tmp, path)
    log.warning("save_config() wrote OK: exists=%s size=%s",
                path.exists(),
                path.stat().st_size if path.exists() else None)

