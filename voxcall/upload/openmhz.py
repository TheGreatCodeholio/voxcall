from __future__ import annotations
import json
import os
import urllib3
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

@dataclass
class OpenMHzClient:
    api_key: str
    short_name: str
    tgid: str
    freq_mhz: str  # string from config

    def upload(self, fname: str, start_time: float, duration: float) -> bool:
        if not (self.api_key and self.short_name and self.tgid and self.freq_mhz):
            log.error("OpenMHz API Key, tgid, freq, or Short Name not found.")
            return False

        freq = float(self.freq_mhz) * 1e6
        source_list = []

        http = urllib3.PoolManager()
        url = f"https://api.openmhz.com/{self.short_name}/upload"
        audio_data = open(fname, "rb").read()

        r = http.request(
            "POST",
            url,
            fields={
                "call": (os.path.basename(fname), audio_data, "application/octet-stream"),
                "freq": str(freq),
                "error_count": str(0),
                "spike_count": str(0),
                "start_time": str(start_time),
                "stop_time": str(start_time + duration),
                "call_length": str(duration),
                "talkgroup_num": str(self.tgid),
                "emergency": str(0),
                "api_key": self.api_key,
                "source_list": json.dumps(source_list),
            },
            timeout=30,
        )

        if r.status != 200:
            log.debug("initial connect failed with status %s", r.status)
            log.debug(r.data)
            return False

        log.debug("upload to OpenMHz OK")
        return True
