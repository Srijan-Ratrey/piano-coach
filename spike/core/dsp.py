"""Framing and spectrum. The entire DSP surface of the spike is in this file.

Kept to `numpy.fft.rfft` and an explicitly-written Hann window so that the
browser port is a transcription rather than a reimplementation (DECISION #16).

One subtlety worth naming, because it is the classic source of port drift:
`numpy.hanning(N)` is the *symmetric* window (divisor N-1), while the periodic
window (divisor N) is what STFT analysis wants and what every JS FFT example
uses. We build the periodic form by hand below. A symmetric/periodic mismatch
between this file and the JS port would shift every magnitude slightly and
quietly invalidate the tuned thresholds.
"""

from __future__ import annotations

import numpy as np

_WINDOW_CACHE: dict[int, np.ndarray] = {}


def hann_periodic(n: int) -> np.ndarray:
    """Periodic Hann window: ``0.5 - 0.5 * cos(2*pi*k/n)`` for k in [0, n).

    Note the divisor is `n`, not `n - 1`. See the module docstring.
    """
    cached = _WINDOW_CACHE.get(n)
    if cached is None:
        k = np.arange(n, dtype=np.float64)
        cached = 0.5 - 0.5 * np.cos(2.0 * np.pi * k / n)
        _WINDOW_CACHE[n] = cached
    return cached


def frame_count(num_samples: int, fft_size: int, hop: int) -> int:
    """How many whole frames fit in a signal of this length."""
    if num_samples < fft_size:
        return 0
    return 1 + (num_samples - fft_size) // hop


def iter_frames(samples: np.ndarray, fft_size: int, hop: int):
    """Yield successive un-windowed frames of `fft_size` samples.

    Trailing samples that do not fill a whole frame are dropped rather than
    zero-padded: a partial frame has a different effective window and would
    produce a magnitude spectrum that is not comparable with its neighbours.
    """
    for i in range(frame_count(len(samples), fft_size, hop)):
        start = i * hop
        yield samples[start : start + fft_size]


def spectrum(frame: np.ndarray, fft_size: int | None = None) -> np.ndarray:
    """Windowed magnitude spectrum of one frame.

    Returns ``fft_size // 2 + 1`` non-negative magnitudes (not power, not dB).
    Magnitudes are used throughout because chroma sums them linearly; squaring
    would over-weight the loudest partial and make the fundamental-vs-harmonic
    weighting in `chroma.py` mean something different than intended.
    """
    n = fft_size if fft_size is not None else len(frame)
    if len(frame) != n:
        raise ValueError(f"frame length {len(frame)} != fft_size {n}")
    return np.abs(np.fft.rfft(frame * hann_periodic(n)))


def bin_frequencies(fft_size: int, sample_rate: int) -> np.ndarray:
    """Centre frequency of each rfft bin, in Hz."""
    return np.fft.rfftfreq(fft_size, d=1.0 / sample_rate)


def rms(frame: np.ndarray) -> float:
    """Root-mean-square level of a frame, on the same scale as the samples
    (so roughly 0.0 to 1.0 for normalised float audio)."""
    if len(frame) == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(frame, dtype=np.float64))))


def clipped_fraction(samples: np.ndarray, threshold: float) -> float:
    """Fraction of samples at or beyond `threshold` in absolute value.

    Surfaced everywhere because a clipped recording generates harmonic
    distortion that looks exactly like extra notes — it would corrupt the
    extra-note rejection measurement while appearing to be a real result.
    """
    if len(samples) == 0:
        return 0.0
    return float(np.mean(np.abs(samples) >= threshold))
