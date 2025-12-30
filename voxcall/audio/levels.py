from __future__ import annotations
from numpy import short, frombuffer, log10

def bytes_to_samples(raw: bytes):
    return frombuffer(raw, dtype=short)

def pick_channel(samples, channel: str):
    if channel == "left":
        return samples[0::2]
    if channel == "right":
        return samples[1::2]
    return samples

def peak(samples) -> int:
    # samples is numpy array
    return int(max(abs(samples))) if len(samples) else 0

def level_ui_scale(samples) -> int:
    """
    Matches your UI bar math:
      max(100 - int(log10(peak/32768)*10/-25*100), 3)
    """
    p = max(peak(samples), 1)
    v = 100 - int(log10(p / 32768.0) * 10 / -25.0 * 100)
    return max(v, 3)

def level_ui_value(samples) -> float:
    """
    The non-int version you used for threshold comparisons.
    """
    p = max(peak(samples), 1.0)
    return 100 - (log10(p / 32768.0) * 10 / -25.0 * 100)
