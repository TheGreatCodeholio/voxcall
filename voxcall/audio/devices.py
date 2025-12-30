from __future__ import annotations
import pyaudio

def list_input_devices() -> tuple[list[str], dict[str, int], dict[int, str]]:
    p = pyaudio.PyAudio()
    try:
        input_devices: list[str] = []
        name_to_index: dict[str, int] = {}

        info = p.get_host_api_info_by_index(0)
        numdevices = info.get("deviceCount", 0)

        for i in range(numdevices):
            dev = p.get_device_info_by_host_api_device_index(0, i)
            if dev.get("maxInputChannels", 0) > 0:
                name = dev.get("name", f"Device {i}")
                input_devices.append(name)
                name_to_index[name] = i

        index_to_name = {v: k for k, v in name_to_index.items()}
        return input_devices, name_to_index, index_to_name
    finally:
        p.terminate()
