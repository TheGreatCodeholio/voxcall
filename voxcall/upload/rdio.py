from __future__ import annotations
import datetime
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

    def upload(self, fname: str):
        if not (self.api_url and self.api_key and self.system and self.talkgroup):
            log.info("No rdio-scanner config detected, skipping upload to rdio-scanner API")
            return

        http = urllib3.PoolManager()
        audio_data = open(fname, "rb").read()
        r = http.request(
            "POST",
            self.api_url,
            fields={
                "key": self.api_key,
                "dateTime": datetime.datetime.utcnow().isoformat() + "Z",
                "system": str(self.system),
                "talkgroup": str(self.talkgroup),
                "audio": (fname, audio_data, "application/octet-stream"),
            },
            timeout=30,
        )
        if r.status != 200:
            log.debug("initial connect failed with status %s", r.status)
            log.debug(r.data)
        else:
            log.debug("upload to rdio-scanner OK")
