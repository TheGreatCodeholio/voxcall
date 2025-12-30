from __future__ import annotations

import logging
from typing import Iterable, Optional

import pyaudio

from voxcall.config import AudioCfg

log = logging.getLogger("voxcall.audio")

FORMAT = pyaudio.paInt16


def _first_supported_rate(
        pa: pyaudio.PyAudio,
        device_index: int,
        channels: int,
        prefer: Optional[int],
        also_try: Iterable[int],
        monitor_output: bool,
        output_device_index: Optional[int],
) -> int:
    dev = pa.get_device_info_by_index(device_index)
    default_sr = int(round(dev.get("defaultSampleRate", 48000)))

    candidates: list[int] = []

    if prefer and prefer > 0:
        candidates.append(int(prefer))
    candidates.append(default_sr)

    for r in also_try:
        r = int(r)
        if r not in candidates:
            candidates.append(r)

    last_exc: Exception | None = None
    for rate in candidates:
        try:
            if monitor_output:
                pa.is_format_supported(
                    rate,
                    input_device=device_index,
                    input_channels=channels,
                    input_format=FORMAT,
                    output_device=output_device_index,
                    output_channels=1,
                    output_format=FORMAT,
                )
            else:
                pa.is_format_supported(
                    rate,
                    input_device=device_index,
                    input_channels=channels,
                    input_format=FORMAT,
                )
            return rate
        except Exception as e:
            last_exc = e

    raise OSError(
        f"No supported input sample rate found for device {device_index} (channels={channels}). "
        f"Last error: {last_exc!r}"
    )


class AudioStream:
    def __init__(self, cfg: AudioCfg):
        self.cfg = cfg
        self._pa: pyaudio.PyAudio | None = None
        self._stream = None

        self.channels = 2 if cfg.in_channel in ("left", "right") else 1

        # set during open()
        self.rate: int | None = None
        self.chunk_frames: int | None = None

    def open(self) -> None:
        self._pa = pyaudio.PyAudio()

        out_idx = None
        if self.cfg.monitor_output:
            out_idx = None if self.cfg.output_device_index < 0 else self.cfg.output_device_index

        rate = _first_supported_rate(
            pa=self._pa,
            device_index=self.cfg.device_index,
            channels=self.channels,
            prefer=self.cfg.prefer_rate if self.cfg.prefer_rate > 0 else None,
            also_try=(48000, 44100, 32000, 24000, 22050, 16000, 11025, 8000),
            monitor_output=self.cfg.monitor_output,
            output_device_index=out_idx,
        )

        rectime_s = float(self.cfg.rectime or 0.1)
        chunk_frames = max(64, int(round(rate * rectime_s)))

        self.rate = rate
        self.chunk_frames = chunk_frames

        log.info(
            "Audio open: dev=%s ch=%s rate=%s rectime=%.3fs chunk_frames=%s monitor_output=%s",
            self.cfg.device_index,
            self.channels,
            rate,
            rectime_s,
            chunk_frames,
            self.cfg.monitor_output,
        )

        self._stream = self._pa.open(
            format=FORMAT,
            channels=self.channels,
            rate=rate,
            input=True,
            output=bool(self.cfg.monitor_output),
            frames_per_buffer=chunk_frames,
            input_device_index=self.cfg.device_index,
            output_device_index=out_idx,
        )

    def close(self) -> None:
        if self._stream:
            self._stream.close()
            self._stream = None
        if self._pa:
            self._pa.terminate()
            self._pa = None

    def read_chunk(self) -> bytes:
        if not self._stream or not self.chunk_frames:
            raise RuntimeError("AudioStream is not open")
        return self._stream.read(self.chunk_frames, exception_on_overflow=False)

    @property
    def pa(self) -> pyaudio.PyAudio:
        if not self._pa:
            raise RuntimeError("PyAudio not initialized")
        return self._pa
