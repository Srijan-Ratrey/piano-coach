"""The verdict state machine — PLAN's Feature 1, steps 4 through 7.

This is the decision that makes the whole project tractable (DECISIONS #2):

    Verification, not transcription. In wait-mode the app already knows the
    next notes. It checks "are these known pitches present?" rather than
    "what is being played?"

So this class is deliberately *not* a chord recogniser. It takes a known target
and answers one yes/no question about the current frame, subject to four
conditions that must hold simultaneously:

    1. every target pitch is present            (PLAN step 4)
    2. no unexpected pitch is loud              (PLAN step 5, decision A)
    3. an onset was seen since the last reset   (PLAN step 6, decision F)
    4. 1-3 have held for `stability_ms`         (PLAN step 6, decision B)

Dropping any one of these breaks it in a specific, known way: without (2)
overtones make everything read correct; without (3) a ringing chord advances
the song by itself; without (4) a brushed key counts as a note.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..params import Params


def harmonic_pitch_class_offsets(n_harmonics: int) -> tuple[int, ...]:
    """Pitch-class offsets of harmonics 2..n, in semitones mod 12.

    Harmonic h sits ``12*log2(h)`` semitones above the fundamental: the 2nd is
    an octave (0 mod 12), the 3rd a perfect fifth (+7), the 4th two octaves
    (0), the 5th a major third (+4). Used only when
    `Params.harmonic_exclusion` is on.
    """
    offsets = set()
    for h in range(2, n_harmonics + 1):
        offsets.add(int(round(12.0 * np.log2(h))) % 12)
    return tuple(sorted(offsets))


@dataclass
class Verdict:
    """What the verifier concluded about one frame."""

    frame: int
    silent: bool
    onset: bool
    armed: bool
    """True once an onset has been seen since the last reset — condition 3."""
    targets_present: bool
    extras: tuple[int, ...]
    """Indices (pitch classes, or MIDI notes in octave mode) of unexpected
    pitches loud enough to block confirmation."""
    frames_held: int
    confirmed_now: bool
    """True on the single frame where confirmation is first reached."""
    latched: bool
    """True from `confirmed_now` onward until `reset()`."""

    @property
    def matching(self) -> bool:
        """Conditions 1-3 satisfied this frame, stability aside."""
        return self.armed and self.targets_present and not self.extras


@dataclass
class Verifier:
    """Stateful, but pure: no I/O, no globals, no clock.

    Time is counted in frames rather than wall-clock seconds so that offline
    scoring of a WAV and a live microphone session produce identical verdicts
    from identical audio — a property the golden vectors depend on.
    """

    target: tuple[int, ...]
    """Pitch classes 0-11, or MIDI note numbers when `params.octave_mode`."""

    params: Params

    _frame: int = field(default=0, init=False)
    _armed: bool = field(default=False, init=False)
    _held: int = field(default=0, init=False)
    _latched: bool = field(default=False, init=False)
    onset_frame: int | None = field(default=None, init=False)
    confirm_frame: int | None = field(default=None, init=False)

    def __post_init__(self):
        self.target = tuple(sorted(set(self.target)))
        if self.params.octave_mode:
            for m in self.target:
                if not (self.params.midi_low <= m <= self.params.midi_high):
                    raise ValueError(
                        f"target MIDI {m} outside analysed range "
                        f"[{self.params.midi_low}, {self.params.midi_high}]"
                    )
        else:
            for pc in self.target:
                if not (0 <= pc <= 11):
                    raise ValueError(f"target pitch class {pc} outside 0-11")
        self._exempt = self._compute_exempt()

    def _compute_exempt(self) -> frozenset[int]:
        """Indices exempt from the extra-note check, per `harmonic_exclusion`."""
        if not self.params.harmonic_exclusion:
            return frozenset()
        offsets = harmonic_pitch_class_offsets(len(self.params.harmonic_weights))
        if self.params.octave_mode:
            # In octave mode the relevant exemption is the actual harmonic
            # pitch, not a pitch class: harmonic h of MIDI m sits at
            # m + 12*log2(h) semitones.
            exempt = set()
            for m in self.target:
                for h in range(2, len(self.params.harmonic_weights) + 1):
                    exempt.add(m + int(round(12.0 * np.log2(h))))
            return frozenset(exempt)
        return frozenset((pc + off) % 12 for pc in self.target for off in offsets)

    def reset(self) -> None:
        """Clear all state. Called between steps of a song, and between takes
        when scoring — a stale `armed` flag would leak an onset from the
        previous chord into the next one."""
        self._frame = 0
        self._armed = False
        self._held = 0
        self._latched = False
        self.onset_frame = None
        self.confirm_frame = None

    def step(
        self,
        chroma: np.ndarray,
        notes: np.ndarray,
        is_onset: bool,
        frame_rms: float,
    ) -> Verdict:
        """Advance one frame and return the verdict.

        `chroma` is the L1-normalised 12-vector, `notes` the L1-normalised
        per-note energies; which one is consulted depends on `octave_mode`.
        Both are always passed so a caller can switch modes without rewiring.
        """
        frame = self._frame
        self._frame += 1

        silent = frame_rms < self.params.silence_rms
        if silent:
            # Silence is not evidence of anything. Drop the held count but keep
            # `armed`: a note struck and then decaying below the RMS gate should
            # not require a fresh strike if it swells back — and more
            # importantly, resetting `armed` here would let the *next* chord be
            # confirmed with no onset of its own after any quiet gap.
            self._held = 0
            return Verdict(
                frame=frame,
                silent=True,
                onset=False,
                armed=self._armed,
                targets_present=False,
                extras=(),
                frames_held=0,
                confirmed_now=False,
                latched=self._latched,
            )

        if is_onset and not self._armed:
            self._armed = True
            self.onset_frame = frame
        elif is_onset:
            # A second strike restarts the stability clock: whatever was held
            # before belongs to the previous attack.
            self._held = 0

        vector = notes if self.params.octave_mode else chroma
        offset = self.params.midi_low if self.params.octave_mode else 0

        peak = float(vector.max()) if vector.size else 0.0
        if peak <= 0.0:
            self._held = 0
            return Verdict(
                frame=frame,
                silent=False,
                onset=is_onset,
                armed=self._armed,
                targets_present=False,
                extras=(),
                frames_held=0,
                confirmed_now=False,
                latched=self._latched,
            )

        present_level = self.params.present_thresh * peak
        extra_level = self.params.extra_margin * peak

        target_idx = [t - offset for t in self.target]
        targets_present = all(
            0 <= i < vector.size and vector[i] >= present_level for i in target_idx
        )

        target_set = set(target_idx)
        extras = tuple(
            int(i + offset)
            for i in np.flatnonzero(vector > extra_level)
            if int(i) not in target_set and int(i + offset) not in self._exempt
        )

        ok = self._armed and targets_present and not extras
        self._held = self._held + 1 if ok else 0

        confirmed_now = False
        if not self._latched and self._held >= self.params.stability_frames:
            self._latched = True
            self.confirm_frame = frame
            confirmed_now = True

        return Verdict(
            frame=frame,
            silent=False,
            onset=is_onset,
            armed=self._armed,
            targets_present=targets_present,
            extras=extras,
            frames_held=self._held,
            confirmed_now=confirmed_now,
            latched=self._latched,
        )

    def latency_ms(self) -> float | None:
        """Milliseconds from the arming onset to confirmation, or None if the
        target was never confirmed.

        This is the number DECISIONS #3 budgets at 300-700 ms, and it is
        measured from the onset rather than from the start of the take so that
        a player's hesitation before striking does not count against the
        detector.
        """
        if self.confirm_frame is None or self.onset_frame is None:
            return None
        return (self.confirm_frame - self.onset_frame) * self.params.frame_interval_ms
