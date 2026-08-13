"""WAV read/write and microphone helpers.

Uses the standard library's `wave` module rather than `soundfile`, which keeps
the dependency list to three pip packages and avoids a libsndfile native build.
The corpus is 16-bit mono PCM at 48 kHz — plenty for harmonic analysis, and
directly inspectable in any audio editor if a take looks wrong.

This module is kept out of `spike/core/` on purpose: `core/` must stay free of
file and device access so it can be unit-tested and ported without dragging
along an audio backend.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

INT16_SCALE = 32767.0


def write_wav(path: str | Path, samples: np.ndarray, sample_rate: int) -> None:
    """Write mono float samples (nominally -1.0..1.0) as 16-bit PCM."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = np.clip(samples, -1.0, 1.0)
    pcm = np.round(clipped * INT16_SCALE).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())


def read_wav(path: str | Path) -> tuple[np.ndarray, int]:
    """Read a WAV as mono float64 in -1.0..1.0, plus its sample rate.

    Multi-channel files are averaged down to mono. 8/16/32-bit integer PCM is
    supported; anything else raises rather than silently mis-scaling, because a
    quietly wrong scale factor would shift every threshold in the spike.
    """
    with wave.open(str(path), "rb") as w:
        channels = w.getnchannels()
        width = w.getsampwidth()
        rate = w.getframerate()
        raw = w.readframes(w.getnframes())

    if width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float64) / INT16_SCALE
    elif width == 1:
        # 8-bit WAV is unsigned, centred on 128.
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128.0) / 128.0
    elif width == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2147483647.0
    else:
        raise ValueError(f"unsupported sample width {width * 8}-bit in {path}")

    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)

    return data, rate


# --- microphone ------------------------------------------------------------


def default_input_name() -> str:
    """Human-readable name of the current default input device."""
    import sounddevice as sd

    index = sd.default.device[0]
    if index is None or index < 0:
        return "unknown"
    return str(sd.query_devices(index)["name"])


def device_name(index: int | None) -> str:
    """Name of a device by index, or of the default input when index is None."""
    if index is None:
        return default_input_name()
    import sounddevice as sd

    return str(sd.query_devices(index)["name"])


def list_input_devices() -> list[tuple[int, str, int]]:
    """`(index, name, max_input_channels)` for every device that can record."""
    import sounddevice as sd

    return [
        (i, str(d["name"]), int(d["max_input_channels"]))
        for i, d in enumerate(sd.query_devices())
        if int(d["max_input_channels"]) > 0
    ]


MIC_PERMISSION_HINT = (
    "Input is completely silent.\n"
    "On macOS this almost always means the app running Python has not been\n"
    "granted microphone access — the OS returns a stream of zeros rather than\n"
    "raising an error, so nothing looks broken until the numbers are all zero.\n"
    "Fix: System Settings > Privacy & Security > Microphone, enable the entry\n"
    "for Terminal (or iTerm, or Visual Studio Code — whichever is running\n"
    "this), then restart that app completely."
)


def record(seconds: float, sample_rate: int, device: int | None = None) -> np.ndarray:
    """Block for `seconds` and return mono float64 samples."""
    import sounddevice as sd

    frames = int(seconds * sample_rate)
    buf = sd.rec(
        frames,
        samplerate=sample_rate,
        channels=1,
        dtype="float32",
        device=device,
    )
    sd.wait()
    return buf[:, 0].astype(np.float64)
