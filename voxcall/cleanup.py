from __future__ import annotations
import errno
import os
import time
from pathlib import Path
from shutil import copyfile
import logging

log = logging.getLogger(__name__)

def cleanup_audio_files(wav_path: Path, save_audio: bool, archive_dir: str | Path = "audiosave"):
    """
    If save_audio is True, copy mp3 to archive_dir (default: audiosave/).
    Then remove temp mp3/m4a after a short delay (keeps your uploader hack).
    """
    mp3 = wav_path.with_suffix(".mp3")
    m4a = wav_path.with_suffix(".m4a")

    if save_audio:
        dest_dir = Path(archive_dir or "audiosave").expanduser()
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            log.exception("Failed creating archive directory: %s", dest_dir)
            raise

        if mp3.exists():
            dest = dest_dir / mp3.name
            log.debug("Archiving mp3: %s -> %s", mp3, dest)
            try:
                copyfile(str(mp3), str(dest))
            except Exception:
                log.exception("Failed to archive mp3 to %s", dest)
        else:
            log.debug("Archive enabled but mp3 does not exist yet: %s", mp3)

    time.sleep(10)  # keep your hack to ensure uploader grabbed files
    log.debug("Removing temporary audio files")

    for p in (mp3, m4a):
        try:
            os.remove(str(p))
        except FileNotFoundError:
            pass
        except Exception:
            log.exception("Failed removing temp file: %s", p)
