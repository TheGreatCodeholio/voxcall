from __future__ import annotations

import time
import wave
import threading
from dataclasses import dataclass
from pathlib import Path
import logging

from numpy import array
import pyaudio

from voxcall.audio.stream import AudioStream, FORMAT
from voxcall.audio.levels import (
    bytes_to_samples,
    pick_channel,
    level_ui_scale,
    level_ui_value,
    peak,
)
from voxcall.encode.ffmpeg import wav_to_mp3_m4a
from voxcall.cleanup import cleanup_audio_files
from voxcall.upload.broadcastify import BcfyClient
from voxcall.upload.rdio import RdioClient
from voxcall.upload.openmhz import OpenMHzClient
from voxcall.config import AppCfg

log = logging.getLogger(__name__)


@dataclass
class UiHooks:
    # all optional; GUI passes these in, CLI passes none
    set_status: callable | None = None
    set_status_color: callable | None = None
    set_bar: callable | None = None


def _safe_call(fn, *a, **kw):
    if fn:
        try:
            fn(*a, **kw)
        except Exception:
            pass


class VoxCallEngine:
    def __init__(self, cfg: AppCfg, version: str, hooks: UiHooks | None = None):
        self.cfg = cfg
        self.version = version
        self.hooks = hooks or UiHooks()
        self._stop = threading.Event()

        # stream consumes AudioCfg directly; it will probe a valid sample rate
        self.stream = AudioStream(cfg.audio)

        self.bcfy = BcfyClient(
            api_key=cfg.bcfy.api_key,
            system_id=cfg.bcfy.system_id,
            slot_id=cfg.bcfy.slot_id,
            freq_mhz=cfg.bcfy.freq_mhz,
            version=version,
        )
        self.rdio = RdioClient(
            api_url=cfg.rdio.api_url,
            api_key=cfg.rdio.api_key,
            system=cfg.rdio.system,
            talkgroup=cfg.rdio.talkgroup,
        )
        icad = getattr(cfg, "icad_dispatch", None)
        self.icad_dispatch = RdioClient(
            api_url=getattr(icad, "api_url", "") if icad else "",
            api_key=getattr(icad, "api_key", "") if icad else "",
            system=getattr(icad, "system", "") if icad else "",
            talkgroup=getattr(icad, "talkgroup", "") if icad else "",
        )
        self.openmhz = OpenMHzClient(
            api_key=cfg.openmhz.api_key,
            short_name=cfg.openmhz.short_name,
            tgid=cfg.openmhz.tgid,
            freq_mhz=cfg.bcfy.freq_mhz,
        )

        self._rec_debounce_counter = 0

    def stop(self):
        self._stop.set()

    def run_forever(self):
        self.stream.open()
        try:
            self._loop()
        finally:
            self.stream.close()

    def _record_rectime_samples(self) -> tuple[bytes, object]:
        """
        Read *one* rectime chunk worth of audio (raw bytes + numpy samples, channel-picked).
        AudioStream already sizes the chunk based on cfg.audio.rectime.
        """
        raw = self.stream.read_chunk()
        samples = bytes_to_samples(raw)
        samples = pick_channel(samples, self.cfg.audio.in_channel)
        return raw, samples

    def _loop(self):
        last_api_attempt = 0.0
        counter = 0

        _safe_call(self.hooks.set_status, "Waiting For Audio")
        _safe_call(self.hooks.set_status_color, "blue")

        while not self._stop.is_set():
            if time.time() - last_api_attempt > 10 * 60:
                threading.Thread(target=self.bcfy.heartbeat, daemon=True).start()
                last_api_attempt = time.time()

            raw, samples = self._record_rectime_samples()

            # UI bar update (same cadence)
            if peak(samples) == 0:
                _safe_call(self.hooks.set_bar, 1)
            elif counter >= 6:
                _safe_call(self.hooks.set_bar, level_ui_scale(samples))
                counter = 0
            counter += 1

            # threshold / debounce (keep original "UI scale" behavior)
            lvl = level_ui_value(samples)
            if lvl > self.cfg.audio.record_threshold or self.cfg.audio.record_threshold == 0:
                self._rec_debounce_counter += 1
                log.debug("Level: %s Threshold: %s", lvl, self.cfg.audio.record_threshold)
            else:
                self._rec_debounce_counter = 0

            if self._rec_debounce_counter >= 2:
                self._rec_debounce_counter = 0
                self._handle_recording()
                last_api_attempt = time.time()

    def _handle_recording(self):
        log.debug("threshold exceeded")
        start_time = time.time()

        rectime = float(self.cfg.audio.rectime or 0.1)
        vox_silence_time = float(self.cfg.audio.vox_silence_time or 2.0)
        timeout_time_sec = int(self.cfg.audio.timeout_time_sec or 120)

        # how many consecutive rectime chunks count as "silence"
        silence_needed = max(1, int(round(vox_silence_time / rectime)))
        timeout_needed = max(1, int(round(timeout_time_sec / rectime)))

        quiet_chunks = 0
        total_chunks = 0

        # store each rectime-chunk so we can trim trailing silence cleanly
        chunks: list[bytes] = []

        log.debug("Waiting for Silence %s", time.strftime("%H:%M:%S on %m/%d/%y"))
        _safe_call(self.hooks.set_status, "Recording")
        _safe_call(self.hooks.set_status_color, "green")

        timed_out = False
        while quiet_chunks < silence_needed and not self._stop.is_set():
            if total_chunks > timeout_needed:
                timed_out = True
                _safe_call(self.hooks.set_status, "RECORDING TIMED OUT")
                _safe_call(self.hooks.set_status_color, "red")
                log.debug("RECORDING TIMED OUT")

            raw, samples = self._record_rectime_samples()

            # only keep audio if not timed out (original behavior)
            if not timed_out:
                chunks.append(raw)

            _safe_call(self.hooks.set_bar, 1 if peak(samples) == 0 else level_ui_scale(samples))

            lvl = level_ui_value(samples)
            if lvl < self.cfg.audio.record_threshold and self.cfg.audio.record_threshold != 0:
                quiet_chunks += 1
            else:
                quiet_chunks = 0

            total_chunks += 1

        log.debug("Done recording %s", time.strftime("%H:%M:%S on %m/%d/%y"))

        if not chunks:
            _safe_call(self.hooks.set_status, "No audio captured")
            _safe_call(self.hooks.set_status_color, "red")
            return

        # Trim trailing silence (this is what the original code *meant* to do)
        if len(chunks) > silence_needed:
            chunks = chunks[:-silence_needed]

        alldata = b"".join(chunks)

        # bytes -> samples (pick channel) for duration + wav writing
        samples = bytes_to_samples(alldata)
        samples = pick_channel(samples, self.cfg.audio.in_channel)

        if not self.stream.rate:
            raise RuntimeError("Stream rate not set (stream not open?)")

        duration = len(samples) / float(self.stream.rate)

        # mono int16 -> bytes
        wav_bytes = array(samples, dtype="int16").tobytes()

        wav_name = f"{round(time.time())}-{self.cfg.bcfy.slot_id}.wav"
        wav_path = Path(wav_name)

        wf = wave.open(str(wav_path), "wb")
        wf.setnchannels(1)
        wf.setsampwidth(self.stream.pa.get_sample_size(FORMAT))
        wf.setframerate(self.stream.rate)  # IMPORTANT: capture rate, not a constant
        wf.writeframes(wav_bytes)
        wf.close()

        log.debug("done writing WAV %s", time.strftime("%H:%M:%S on %m/%d/%y"))
        log.debug("%s", wav_path)

        try:
            # Keep legacy output sample rate even if capture is 48k/44.1k/etc.
            mp3_path, m4a_path = wav_to_mp3_m4a(wav_path, self.cfg.mp3_bitrate, ar=22050)

            log.debug("done converting to MP3 %s", time.strftime("%H:%M:%S on %m/%d/%y"))
            log.debug("done converting to M4A %s", time.strftime("%H:%M:%S on %m/%d/%y"))

            # match original: delete wav after encode
            try:
                wav_path.unlink()
            except FileNotFoundError:
                pass

        except Exception:
            log.exception("Got exception during ffmpeg encode")
            _safe_call(self.hooks.set_status, "Encode failed")
            _safe_call(self.hooks.set_status_color, "red")
            return

        # fan-out uploads + cleanup (threads like original)
        threading.Thread(target=self.bcfy.upload_mp3, args=(str(mp3_path), duration), daemon=True).start()
        threading.Thread(target=self.rdio.upload, args=(str(mp3_path),), daemon=True).start()
        threading.Thread(target=self.icad_dispatch.upload, args=(str(mp3_path),), daemon=True).start()
        threading.Thread(target=self.openmhz.upload, args=(str(m4a_path), start_time, duration), daemon=True).start()

        # cleanup derives mp3/m4a names from wav_path (even if wav is deleted)
        threading.Thread(
            target=cleanup_audio_files,
            kwargs={
                "wav_path": wav_path,
                "save_audio": self.cfg.save_audio,
                "archive_dir": getattr(self.cfg, "archive_dir", "audiosave"),
            },
            daemon=True
        ).start()

        log.debug("duration: %s sec", duration)
        log.debug("waiting for audio %s", time.strftime("%H:%M:%S on %m/%d/%y"))
        _safe_call(self.hooks.set_status, "Waiting For Audio")
        _safe_call(self.hooks.set_status_color, "blue")
