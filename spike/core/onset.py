"""Onset detection by spectral flux.

Open decision F, and not optional. From PLAN's risk list:

    "Onset gating is required so a still-ringing chord doesn't auto-satisfy the
    next step."

Concretely: the player holds a C major triad, the app advances, and the next
step is C major again (or shares its pitch classes). Without an onset
requirement the still-decaying sound satisfies the new step instantly, the app
advances again, and the song fast-forwards through itself while the player sits
there. Sustain pedal makes this worse by design.

Flux is measured as the sum of positive bin-to-bin magnitude increases between
consecutive frames — energy *appearing*, which is what a hammer strike does,
while decay contributes nothing because those deltas are negative.
"""

from __future__ import annotations

from collections import deque

import numpy as np

from ..params import Params

MIN_HISTORY_FRAMES = 3
"""Frames of flux history required before any onset may be reported."""


def spectral_flux(spec: np.ndarray, prev_spec: np.ndarray | None) -> float:
    """Sum of positive magnitude increases from `prev_spec` to `spec`.

    Half-wave rectified on purpose: a decaying note produces large *negative*
    deltas, and counting those would make the release of one chord look like
    the attack of the next.
    """
    if prev_spec is None or len(prev_spec) != len(spec):
        return 0.0
    return float(np.sum(np.maximum(spec - prev_spec, 0.0)))


class OnsetDetector:
    """Adaptive-threshold onset detector over a rolling median of recent flux.

    A fixed flux threshold cannot work: flux scales with absolute loudness, so
    any constant is simultaneously too high for soft playing and too low for
    loud playing. Comparing against the recent median instead asks the
    scale-free question "is this frame unusually energetic *for this passage*",
    which survives both dynamics and mic gain.
    """

    def __init__(self, params: Params):
        self.params = params
        self._prev_spec: np.ndarray | None = None
        self._history: deque[float] = deque(maxlen=params.onset_window_frames)
        self._since_onset = params.onset_refractory_frames
        self.last_flux = 0.0
        self.last_threshold = 0.0

    def reset(self) -> None:
        self._prev_spec = None
        self._history.clear()
        self._since_onset = self.params.onset_refractory_frames
        self.last_flux = 0.0
        self.last_threshold = 0.0

    def step(self, spec: np.ndarray) -> bool:
        """Feed one frame's magnitude spectrum; return whether it is an onset."""
        self._since_onset += 1
        flux = spectral_flux(spec, self._prev_spec)
        self._prev_spec = spec.copy()

        total = float(np.sum(spec))
        floor = self.params.onset_flux_floor * total

        # Median over history *excluding* the current frame, so a strong onset
        # does not raise the very bar it has to clear.
        warming_up = len(self._history) < MIN_HISTORY_FRAMES
        if warming_up:
            threshold = floor
        else:
            median = float(np.median(self._history))
            threshold = max(self.params.onset_flux_ratio * median, floor)

        self._history.append(flux)
        self.last_flux = flux
        self.last_threshold = threshold

        # No onsets until the passage has been observed for a few frames.
        # Otherwise the very first frames — where the only thing to compare
        # against is the absolute floor — report room noise as a strike, which
        # arms the verifier before a single key is touched and silently
        # cancels the onset gate that decision F depends on.
        if warming_up:
            return False

        if flux <= threshold or flux <= 0.0:
            return False

        # Refractory gate. The flux history is still updated above, so a
        # suppressed burst continues to inform the adaptive median — only the
        # reported onset is swallowed.
        if self._since_onset < self.params.onset_refractory_frames:
            return False

        self._since_onset = 0
        return True
