from __future__ import annotations
import datetime
from pathlib import Path

import urllib3
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

@dataclass
class RdioClient:
    api_url: str
    api_key: str
    system: str
    talkgroup: str

    def upload(self, fname: str, is_icad: bool = False):
        # Normalize + detect missing config keys (treat whitespace as missing)
        required = {
            "api_url": (self.api_url or "").strip(),
            "api_key": (self.api_key or "").strip(),
            "system":  (self.system or "").strip(),
            "talkgroup": (self.talkgroup or "").strip(),
        }
        missing = [k for k, v in required.items() if not v]

        target = "iCAD dispatch API" if is_icad else "rdio-scanner API"
        tab = "iCAD Dispatch" if is_icad else "RDIO"

        if missing:
            log.warning(
                "Skipping upload to %s: missing config field(s): %s (set these in the %s tab / config).",
                target,
                ", ".join(missing),
                tab,
            )
            return

        self.api_url = required["api_url"]
        self.api_key = required["api_key"]
        self.system = required["system"]
        self.talkgroup = required["talkgroup"]

        http = urllib3.PoolManager()

        try:
            with open(fname, "rb") as f:
                audio_data = f.read()
        except Exception as e:
            log.warning("Failed to read audio file for upload to %s: %s", target, e)
            return

        try:
            r = http.request(
                "POST",
                self.api_url,
                fields={
                    "key": self.api_key,
                    "dateTime": datetime.datetime.utcnow().isoformat() + "Z",
                    "system": str(self.system),
                    "talkgroup": str(self.talkgroup),
                    "audio": (Path(fname).name, audio_data, "application/octet-stream"),
                },
                timeout=30,
            )
        except Exception as e:
            log.warning("Upload to %s failed (request error): %s", target, e)
            return

        # Treat any 2xx as success (200/201/204 etc.)
        if 200 <= int(getattr(r, "status", 0)) < 300:
            log.info(
                "Upload to %s OK (status=%s, system=%s, talkgroup=%s, file=%s)",
                target, r.status, self.system, self.talkgroup, Path(fname).name
            )
            return

        # Failure: log status + response body (decoded safely)
        body = getattr(r, "data", b"")
        try:
            body_text = body.decode("utf-8", errors="replace") if isinstance(body, (bytes, bytearray)) else str(body)
        except Exception:
            body_text = "<unreadable response body>"

        log.warning("Upload to %s failed (status=%s)", target, getattr(r, "status", "?"))
        if body_text:
            log.debug("Response body from %s: %s", target, body_text[:4000])  # cap to avoid huge logs
