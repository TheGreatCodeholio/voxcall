from __future__ import annotations
import subprocess
from pathlib import Path

def _creationflags():
    try:
        return subprocess.CREATE_NO_WINDOW
    except Exception:
        return 0

def wav_to_mp3_m4a(wav_path: Path, mp3_bitrate: int, ar: int = 22050) -> tuple[Path, Path]:
    flags = _creationflags()
    mp3_path = wav_path.with_suffix(".mp3")
    m4a_path = wav_path.with_suffix(".m4a")

    subprocess.check_call(
        ["ffmpeg", "-y", "-i", str(wav_path), "-b:a", str(mp3_bitrate), "-ar", str(ar), str(mp3_path)],
        creationflags=flags,
    )
    subprocess.check_call(
        ["ffmpeg", "-y", "-i", str(wav_path), "-b:a", str(mp3_bitrate), "-ar", str(ar), str(m4a_path)],
        creationflags=flags,
    )
    return mp3_path, m4a_path
