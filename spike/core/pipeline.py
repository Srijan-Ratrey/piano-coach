"""Wires dsp → chroma → onset → verify into one pass over a signal.

Deliberately still I/O-free, so the exact same code path serves the live meter,
the offline scorer, the plotter and the golden-vector exporter. If the live and
offline paths were assembled separately they would drift, and a spike whose
live behaviour differs from its measured behaviour proves nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..params import Params
from . import dsp
from .chroma import ChromaExtractor
from .onset import OnsetDetector
from .verify import Verdict, Verifier


@dataclass
class FrameResult:
    """Everything computed for one analysis frame."""

    index: int
    time_ms: float
    rms: float
    chroma: np.ndarray
    notes: np.ndarray
    flux: float
    flux_threshold: float
    onset: bool
    verdict: Verdict | None
    """None when no target was supplied (free-running meter mode)."""


class Analyser:
    """Stateful per-frame analyser. Feed it frames in order.

    `set_target()` re-arms it for a new step, which in the real app is what
    happens every time the wait-mode loop advances.
    """

    def __init__(self, params: Params, target: tuple[int, ...] | None = None):
        self.params = params
        self.extractor = ChromaExtractor(params)
        self.onset = OnsetDetector(params)
        self.verifier = Verifier(target, params) if target is not None else None
        self._index = 0

    def set_target(self, target: tuple[int, ...] | None) -> None:
        """Point the verifier at a new step.

        The onset detector is deliberately *not* reset. Its flux history
        describes the audio stream, not the step, and clearing it at every step
        boundary would put the detector back into its warm-up state each time —
        where it cannot yet distinguish a strike from room noise. In the real
        wait-mode loop that happens on every advance, so resetting here would
        undo onset gating exactly when it matters most.
        """
        self.verifier = Verifier(target, self.params) if target is not None else None
        self._index = 0

    def reset(self) -> None:
        self._index = 0
        self.onset.reset()
        if self.verifier is not None:
            self.verifier.reset()

    def push(self, frame: np.ndarray) -> FrameResult:
        """Analyse one un-windowed frame of `params.fft_size` samples."""
        spec = dsp.spectrum(frame, self.params.fft_size)
        chroma, notes = self.extractor.chroma(spec)
        is_onset = self.onset.step(spec)
        level = dsp.rms(frame)

        verdict = None
        if self.verifier is not None:
            verdict = self.verifier.step(chroma, notes, is_onset, level)

        result = FrameResult(
            index=self._index,
            time_ms=self._index * self.params.frame_interval_ms,
            rms=level,
            chroma=chroma,
            notes=notes,
            flux=self.onset.last_flux,
            flux_threshold=self.onset.last_threshold,
            onset=is_onset,
            verdict=verdict,
        )
        self._index += 1
        return result


@dataclass
class FrameData:
    """Front-end output for a whole signal, independent of any target.

    Splitting the pipeline here is what makes `sweep.py` affordable. Chroma and
    onsets depend on the transform (`fft_size`, `hop`, harmonic weights);
    thresholds, stability and octave mode affect only the verifier. So the
    expensive half is computed once per FFT size and the cheap half is replayed
    across the whole threshold grid — hundreds of combinations for the cost of
    one pass of FFTs.
    """

    chroma: np.ndarray      # (frames, 12)
    notes: np.ndarray       # (frames, n_notes)
    onsets: np.ndarray      # (frames,) bool
    rms: np.ndarray         # (frames,)

    def __len__(self) -> int:
        return len(self.rms)


def precompute(samples: np.ndarray, params: Params) -> FrameData:
    """Run the target-independent front end over a signal."""
    extractor = ChromaExtractor(params)
    onset = OnsetDetector(params)

    chroma_rows, note_rows, onset_flags, levels = [], [], [], []
    for frame in dsp.iter_frames(samples, params.fft_size, params.hop):
        spec = dsp.spectrum(frame, params.fft_size)
        c, n = extractor.chroma(spec)
        chroma_rows.append(c)
        note_rows.append(n)
        onset_flags.append(onset.step(spec))
        levels.append(dsp.rms(frame))

    n_notes = params.midi_high - params.midi_low + 1
    return FrameData(
        chroma=np.array(chroma_rows) if chroma_rows else np.zeros((0, 12)),
        notes=np.array(note_rows) if note_rows else np.zeros((0, n_notes)),
        onsets=np.array(onset_flags, dtype=bool),
        rms=np.array(levels),
    )


@dataclass
class ReplayResult:
    confirmed: bool
    latency_ms: float | None
    peak_hold: int
    extras: dict[int, int]


def replay(data: FrameData, target: tuple[int, ...], params: Params) -> ReplayResult:
    """Re-run only the verifier over precomputed frames.

    `params` here may differ from the one used for `precompute` in every field
    the verifier reads, but must match in the transform fields — passing a
    different `fft_size` would silently score one signal's frames against
    another signal's thresholds.
    """
    verifier = Verifier(target, params)
    confirmed = False
    latency = None
    onset_frame = None
    peak_hold = 0
    extras: dict[int, int] = {}

    for i in range(len(data)):
        v = verifier.step(data.chroma[i], data.notes[i], bool(data.onsets[i]), float(data.rms[i]))
        peak_hold = max(peak_hold, v.frames_held)
        if v.onset and onset_frame is None:
            onset_frame = v.frame
        for e in v.extras:
            extras[e] = extras.get(e, 0) + 1
        if v.confirmed_now:
            confirmed = True
            if onset_frame is not None:
                latency = (v.frame - onset_frame) * params.frame_interval_ms

    return ReplayResult(confirmed, latency, peak_hold, extras)


def analyse(
    samples: np.ndarray,
    target: tuple[int, ...] | None,
    params: Params,
) -> list[FrameResult]:
    """Run a complete signal through the pipeline and return every frame.

    The frame timestamp is the *start* of each window. A window is 170 ms wide
    at the default FFT size, so a confirmation reported at t also depended on
    audio up to t + 170 ms — worth remembering when reading latencies, and the
    reason the latency budget is measured onset-to-confirm rather than in
    absolute time.
    """
    analyser = Analyser(params, target)
    return [
        analyser.push(frame)
        for frame in dsp.iter_frames(samples, params.fft_size, params.hop)
    ]
