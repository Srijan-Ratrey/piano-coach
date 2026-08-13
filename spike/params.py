"""Every tunable constant for the Layer 1 verifier, in one place.

Two reasons this file exists rather than scattering numbers through the code:

1. `sweep.py` overrides these to grid-search an operating point. Open decisions
   A (extra-note strictness), B (stability window) and E (octave handling) in
   DECISIONS.md are answered by sweeping this file, not by arguing about it.
2. The JS port reads its constants from the values recorded here after the
   sweep. One list to copy, one list to keep in sync.

The defaults below are starting guesses, not tuned values. `spike/RESULTS.md`
records what the corpus actually chose.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

# --- Capture ---------------------------------------------------------------

SAMPLE_RATE = 48000
"""Hz. Both the recorder and the analyser assume this; corpus WAVs are written
at this rate. Web Audio's AudioContext typically also runs at 48 kHz on macOS,
which keeps the port honest."""

# --- Analysis window -------------------------------------------------------

FFT_SIZE = 8192
"""Samples. At 48 kHz this is a 170.7 ms window with 5.86 Hz bin spacing.

This is the single most consequential constant. Semitone spacing near C2
(65.41 Hz) is only ~3.9 Hz — *narrower than one bin* — so the bass register is
expected to be the weak point. `sweep.py` also tries 16384 (2.93 Hz bins,
341 ms window) to measure what the extra resolution buys and what it costs in
latency."""

HOP = 1024
"""Samples between successive frames — 21.3 ms at 48 kHz.

Halved from 2048 to cut latency. A verdict can only be reported on a frame
boundary, so the hop is a quantisation error on every confirmation: at 2048 a
confirmation could sit up to 42.7 ms late for no reason other than when the
frames happened to fall. This is the one latency lever with no accuracy cost —
it changes *when* the answer is available, not what the answer is.

Measured cost of the extra frames: one frame (8192-point FFT plus chroma) takes
0.178 ms, so 47 frames/s is about 8% of one core. Affordable in a browser
alongside rendering."""

# --- Pitch range -----------------------------------------------------------

MIDI_LOW = 36
"""C2, 65.41 Hz. Below this an 8192-point FFT cannot separate semitones."""

MIDI_HIGH = 96
"""C7, 2093 Hz. Fundamentals above this are rare in the target repertoire and
their harmonics fall outside the analysis band anyway."""

# --- Chroma construction ---------------------------------------------------

HARMONIC_WEIGHTS = (1.0, 0.5, 0.25, 0.125)
"""Weight applied to each harmonic when accumulating a note's energy:
fundamental, 2nd, 3rd, 4th. Decaying, so the fundamental dominates.

PLAN calls for exactly this ("weight fundamentals over overtones") because a
piano's overtone series makes a low C look like a high C, a high G and a high E
all at once. Summing harmonics *with* the fundamental helps confirm a note is
genuinely sounding rather than being implied by a lower note's partials."""

NEIGHBOURHOOD_CENTS = 25.0
"""Half-width, in cents, of the band searched around each harmonic.

Absorbs instrument tuning offsets and the fact that a partial rarely lands dead
centre in a bin. Specified in cents rather than bins because a fixed bin count
means completely different things at different pitches: ±1 bin is ±5.86 Hz
everywhere, which is a third of a semitone at C5 but nearly *two* semitones at
G2. A fixed-bin window therefore lets neighbouring bass notes read each other's
energy — measured directly on the synthetic corpus, where a G3 caused F#3 and
G#3 to register at 80% of its own strength.

25 cents is a quarter of a semitone either side, so the bands of adjacent
semitones never overlap at any pitch — generous enough for tuning drift and for
the inharmonicity that makes upper partials run sharp, tight enough that a note
cannot borrow its neighbour's fundamental."""

MAX_NEIGHBOUR_BINS = 3
"""Hard cap on the half-width in bins, regardless of what the cents window
works out to. Only binds in the treble, where ±25 cents spans many bins.

Not merely a performance guard: every extra bin in a band is another chance for
the peak-pick to grab noise instead of a partial, and with 61 notes × 4
harmonics those small noise pickups accumulate in the L1 normalisation
denominator and dilute the real chroma peaks."""

# --- Verification thresholds ----------------------------------------------

PRESENT_THRESH = 0.35
"""A target pitch class counts as present when its chroma value is at least
this fraction of the largest chroma value in the frame.

Expressed relative to the frame maximum rather than as an absolute so it is
loudness-independent (PLAN, Feature 1 step 4) *and* insensitive to chord size —
a four-note chord spreads energy thinner than a single note, and an absolute
threshold would silently get stricter as chords get denser."""

EXTRA_MARGIN = 0.50
"""A non-target pitch class blocks confirmation when its chroma value exceeds
this fraction of the frame maximum. Open decision A.

Note the deliberate gap between PRESENT_THRESH (0.35) and this (0.50): a bin
sitting between the two is neither confidently present nor loud enough to
count as a wrong note. That deadband is what stops overtone leakage from
reading as a played wrong note — which PLAN flags as the failure mode that
makes everything read 'correct' if you get it wrong in the other direction."""

STABILITY_MS = 175
"""How long the match condition must hold continuously before confirming.
Open decision B, and by far the largest term in perceived latency.

Measured end-to-end on the JS port: strike to confirmation was 323 ms, of which
the onset was detected at just +24 ms — so the stability window was ~77% of the
delay, and it read as sluggish. Scoring the synthetic corpus across 100-300 ms
showed recall and false-confirms completely flat while latency scaled linearly.

Treat that evidence as weak in one specific way: synthetic tones have almost no
attack transient, and rejecting transients is precisely what this window is
for. So the corpus cannot see the cost of shortening it. 175 ms is a deliberate
step back from the measured-free 100 ms, keeping a real margin until the actual
piano corpus can settle it."""

# --- Onset gating ----------------------------------------------------------

ONSET_FLUX_RATIO = 2.0
"""Spectral flux must exceed this multiple of the recent median flux to count
as a new strike. Open decision F.

Without onset gating a chord still ringing from the previous step satisfies the
next step for free, and the whole wait-mode loop runs away on its own. This is
mandatory, not a refinement."""

ONSET_WINDOW_FRAMES = 42
"""Frames of flux history used for the adaptive median.

Specified in frames but its *purpose* is a wall-clock span: ~0.9 s, long enough
to characterise 'normal' for the current passage, short enough to track a
crescendo. Doubled alongside the halved hop to keep that span the same — left
at 21 it would have silently become a 0.45 s window."""

ONSET_FLUX_FLOOR = 0.10
"""Floor on flux as a fraction of the frame's total magnitude — an onset must
represent at least this much of the spectrum appearing at once.

The floor carries more weight than it first appears, because the adaptive
median collapses toward zero during a long sustain: two times almost-nothing is
still almost-nothing, so without a floor the threshold becomes arbitrarily
sensitive exactly when the note is being held.

What it has to reject is not noise but *beating*. Equal-tempered intervals beat
by design — in a C major triad, C4's third harmonic (784.9 Hz) and G4's second
(784.0 Hz) beat at about 0.9 Hz — and that slow amplitude swell is genuine
positive flux appearing in the middle of a held chord.

Measured on the synthetic corpus: beating peaks at 2.2% of total magnitude, an
isolated strike hits 100%, and the hardest realistic case — F major struck over
a still-ringing pedalled C major — still reaches 50%. 10% sits with a 4x margin
above the noise it must reject and a 5x margin below the quietest strike it
must accept. Re-check this against the real corpus: a room louder than the
synthetic noise floor pushes both numbers up."""

ONSET_REFRACTORY_MS = 170
"""After an onset, suppress further onsets for this long — one strike, one
onset.

This is not a tuning preference, it is forced by the analysis window. A 170 ms
window slides over a hammer strike in four 42.7 ms hops, and energy keeps
increasing for all four, so a single strike produces a *burst* of consecutive
high-flux frames. Left unsuppressed that burst repeatedly restarts the
stability clock and inflates measured latency by the width of the burst.

The *effective* refractory is never shorter than that unavoidable smear —
`Params.onset_refractory_frames` takes the larger of this constant and
`fft_size/hop + 1` frames, so the gate stays correct when `sweep.py` changes
the FFT size (at 16384 the smear alone is 341 ms). This constant is therefore
an additional floor, not the whole story.

The cost is that repeated notes faster than ~6/second read as one attack —
irrelevant here, since wait-mode is player-paced and never advances faster than
the player."""

# --- Gating ----------------------------------------------------------------

SILENCE_RMS = 0.005
"""Frames quieter than this are not analysed at all. Prevents a normalised
chroma vector computed from pure noise — which is meaningless but numerically
just as confident-looking as a real chord."""

# --- Verification mode (the two axes of open decision E) -------------------

OCTAVE_MODE = False
"""False = verify pitch classes only; True = verify exact MIDI notes.

Open decision E. DECISIONS recommends starting pitch-class-only ("more robust
on cheap mics") and tightening to octave only if the spike shows it is
reliable. `sweep.py` runs both and `RESULTS.md` records which one the corpus
supports — the point of the spike is that this stops being a guess."""

HARMONIC_EXCLUSION = False
"""Exempt pitch classes that coincide with a harmonic of a target from the
extra-note check.

The concern this addresses is real: the 3rd harmonic of a note lands a perfect
fifth above it (+7 semitones, mod 12), so an E in the chord radiates some B,
and a strict extras test could reject a correctly-played chord for containing
its own overtones. The concern it *creates* is equally real: exempting those
pitch classes means a genuinely wrong note a fifth above a target goes
unnoticed.

Default off, so the honest unaided number is measured first; `sweep.py` toggles
it to quantify the trade rather than assume it."""

CLIP_THRESHOLD = 0.98
"""Absolute sample value at or above which a sample counts as clipped. Reported
by the recorder and the scorer: a clipped take invalidates its own harmonic
analysis, so it must be visible rather than silently degrading the numbers."""


@dataclass(frozen=True)
class Params:
    """An immutable bundle of the above, so `sweep.py` can vary one axis at a
    time without mutating module globals (which would leak between runs)."""

    sample_rate: int = SAMPLE_RATE
    fft_size: int = FFT_SIZE
    hop: int = HOP
    midi_low: int = MIDI_LOW
    midi_high: int = MIDI_HIGH
    harmonic_weights: tuple[float, ...] = HARMONIC_WEIGHTS
    neighbourhood_cents: float = NEIGHBOURHOOD_CENTS
    max_neighbour_bins: int = MAX_NEIGHBOUR_BINS
    present_thresh: float = PRESENT_THRESH
    extra_margin: float = EXTRA_MARGIN
    stability_ms: float = STABILITY_MS
    onset_flux_ratio: float = ONSET_FLUX_RATIO
    onset_window_frames: int = ONSET_WINDOW_FRAMES
    onset_flux_floor: float = ONSET_FLUX_FLOOR
    onset_refractory_ms: float = ONSET_REFRACTORY_MS
    silence_rms: float = SILENCE_RMS
    octave_mode: bool = OCTAVE_MODE
    harmonic_exclusion: bool = HARMONIC_EXCLUSION

    @property
    def frame_interval_ms(self) -> float:
        """Wall-clock milliseconds between successive frames."""
        return 1000.0 * self.hop / self.sample_rate

    @property
    def stability_frames(self) -> int:
        """`stability_ms` converted to whole frames, minimum 1."""
        return max(1, math.ceil(self.stability_ms / self.frame_interval_ms))

    @property
    def window_smear_frames(self) -> int:
        """Frames a single instantaneous strike occupies as the analysis window
        slides across it. Energy keeps rising for `fft_size/hop` hops after the
        first frame that sees the attack, so the burst spans one more frame
        than that."""
        return self.fft_size // self.hop + 1

    @property
    def onset_refractory_frames(self) -> int:
        """Frames to suppress after an onset — never less than the window
        smear, which is a property of the transform rather than a choice."""
        requested = round(self.onset_refractory_ms / self.frame_interval_ms)
        return max(1, self.window_smear_frames, requested)

    @property
    def bin_hz(self) -> float:
        """Width of one FFT bin in Hz."""
        return self.sample_rate / self.fft_size

    @property
    def window_ms(self) -> float:
        """Duration of one analysis window."""
        return 1000.0 * self.fft_size / self.sample_rate

    def but(self, **overrides) -> "Params":
        """Return a copy with fields replaced — the sweep's only mutator."""
        return replace(self, **overrides)


DEFAULT = Params()
