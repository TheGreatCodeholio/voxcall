from __future__ import annotations
import time
import urllib3
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

@dataclass
class BcfyClient:
    api_key: str
    system_id: str
    slot_id: str
    freq_mhz: str
    version: str

    def base_url(self) -> str:
        if (self.version or "").endswith("DEV"):
            return "https://api.broadcastify.com/call-upload-dev"
        return "https://api.broadcastify.com/call-upload"

    def heartbeat(self):
        if not self.api_key:
            return
        http = urllib3.PoolManager()
        r = http.request("POST", self.base_url(), fields={"apiKey": self.api_key, "systemId": self.system_id, "test": "1"})
        if r.status != 200:
            log.debug("heartbeat failed with status %s", r.status)
            log.debug(r.data)
        else:
            log.debug("heartbeat OK at %s", time.time())

    def upload_mp3(self, fname: str, duration: float):
        if not self.api_key:
            log.info("No BCFY config found, not attempting to upload there")
            return

        http = urllib3.PoolManager()
        r = http.request(
            "POST",
            self.base_url(),
            fields={
                "apiKey": self.api_key,
                "systemId": self.system_id,
                "callDuration": str(duration),
                "ts": fname.split("-")[0],
                "tg": self.slot_id,
                "src": "0",
                "freq": self.freq_mhz,
                "enc": "mp3",
            },
        )

        if r.status != 200:
            log.debug("initial connect failed with status %s", r.status)
            log.debug(r.data)
            return

        resp = r.data.decode("utf-8").split(" ")
        if resp[0] != "0":
            log.debug("error response from server: %s", r.data.decode("utf-8"))
            return

        upload_url = resp[1]
        file_data = open(fname, "rb").read()
        r1 = http.request(
            "PUT",
            upload_url,
            fields={"filefield": (fname, file_data, "audio/mpeg")},
        )
        if r1.status == 200:
            log.debug("upload to BCFY OK")
        else:
            log.debug("upload failed with status %s", r1.status)
            log.debug(r1.data)
