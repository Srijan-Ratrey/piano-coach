"""Synthetic-signal tests for the verification core.

These run before a single note has been recorded, and that is the point: if the
DSP has a bug, real audio will hide it behind plausible-looking numbers and the
go/no-go call will measure the bug instead of the piano.

Everything here is generated from `numpy`, so the suite is deterministic and
needs no corpus, no microphone and no macOS permissions.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from spike.core import dsp
from spike.core.chroma import ChromaExtractor, midi_to_hz, note_name
from spike.core.pipeline import analyse
from spike.core.verify import Verifier, harmonic_pitch_class_offsets
from spike.params import DEFAULT, Params

SR = DEFAULT.sample_rate

# Rough approximation of a piano partial series: the fundamental dominates and
# upper partials fall away. Not physically accurate — it does not need to be.
# Its job is to be *harmonically structured* rather than a pure sine, so the
# chroma extractor is exercised the way real audio exercises it.
PIANO_PARTIALS = (1.0, 0.5, 0.28, 0.16, 0.09, 0.05)


def tone(
    midi: int, seconds: float, amp: float = 0.3, partials=PIANO_PARTIALS
) -> np.ndarray:
    """A steady harmonic tone at the pitch of `midi`."""
    t = np.arange(int(seconds * SR), dtype=np.float64) / SR
    f0 = float(midi_to_hz(midi))
    sig = np.zeros_like(t)
    for h, a in enumerate(partials, start=1):
        if f0 * h >= SR / 2:
            break
        # Vary phase per partial so the peaks do not all stack on sample zero.
        sig += a * np.sin(2.0 * np.pi * f0 * h * t + 0.7 * h)
    return amp * sig / max(1e-12, np.max(np.abs(sig)))


def chord(midis, seconds: float, amp: float = 0.3) -> np.ndarray:
    stack = sum(tone(m, seconds, amp) for m in midis)
    return amp * stack / max(1e-12, np.max(np.abs(stack)))


def struck(signal: np.ndarray, lead_silence: float = 0.4) -> np.ndarray:
    """Prepend silence so the signal has a genuine onset to detect.

    Without this the analyser starts mid-sustain and — correctly — never arms,
    which is the behaviour `test_sustained_tone_without_onset_never_confirms`
    pins down.
    """
    return np.concatenate([np.zeros(int(lead_silence * SR)), signal])


# --- dsp -------------------------------------------------------------------


def test_hann_is_periodic_not_symmetric():
    """The periodic/symmetric distinction is the classic port-drift bug, so it
    gets an explicit test rather than a comment."""
    n = 8
    w = dsp.hann_periodic(n)
    assert w[0] == pytest.approx(0.0)
    # Periodic: no second zero at the end, unlike np.hanning's symmetric form.
    assert w[-1] > 0.0
    assert w[-1] == pytest.approx(0.5 - 0.5 * np.cos(2 * np.pi * (n - 1) / n))
    assert not np.allclose(w, np.hanning(n))


def test_frame_count_and_iteration_agree():
    samples = np.zeros(DEFAULT.fft_size * 3)
    expected = dsp.frame_count(len(samples), DEFAULT.fft_size, DEFAULT.hop)
    frames = list(dsp.iter_frames(samples, DEFAULT.fft_size, DEFAULT.hop))
    assert len(frames) == expected
    assert all(len(f) == DEFAULT.fft_size for f in frames)


def test_short_signal_yields_no_frames():
    """Partial frames are dropped, not zero-padded — a padded frame has a
    different effective window and its magnitudes would not be comparable."""
    assert dsp.frame_count(DEFAULT.fft_size - 1, DEFAULT.fft_size, DEFAULT.hop) == 0


def test_clipped_fraction():
    sig = np.array([0.0, 0.5, 0.99, -1.0])
    assert dsp.clipped_fraction(sig, 0.98) == pytest.approx(0.5)


# --- pitch mapping ---------------------------------------------------------


def test_midi_to_hz_anchors():
    assert float(midi_to_hz(69)) == pytest.approx(440.0)
    assert float(midi_to_hz(60)) == pytest.approx(261.6256, abs=1e-3)
    assert float(midi_to_hz(36)) == pytest.approx(65.4064, abs=1e-3)


def test_note_names():
    assert note_name(60) == "C4"
    assert note_name(69) == "A4"
    assert note_name(36) == "C2"


# --- chroma ----------------------------------------------------------------


def test_pure_sine_lights_its_own_pitch_class():
    t = np.arange(DEFAULT.fft_size, dtype=np.float64) / SR
    sig = 0.5 * np.sin(2.0 * np.pi * float(midi_to_hz(60)) * t)
    ex = ChromaExtractor(DEFAULT)
    chroma, _ = ex.chroma(dsp.spectrum(sig, DEFAULT.fft_size))
    assert int(np.argmax(chroma)) == 0  # C
    assert chroma[0] > 0.5


def test_chroma_is_loudness_independent():
    """L1 normalisation is what lets one threshold serve every dynamic. If this
    breaks, the soft takes in the corpus fail for the wrong reason."""
    ex = ChromaExtractor(DEFAULT)
    quiet = tone(60, 0.5, amp=0.02)[: DEFAULT.fft_size]
    loud = tone(60, 0.5, amp=0.8)[: DEFAULT.fft_size]
    c_quiet, _ = ex.chroma(dsp.spectrum(quiet, DEFAULT.fft_size))
    c_loud, _ = ex.chroma(dsp.spectrum(loud, DEFAULT.fft_size))
    assert np.allclose(c_quiet, c_loud, atol=1e-9)


def test_chroma_sums_to_one_and_silence_gives_zeros():
    ex = ChromaExtractor(DEFAULT)
    chroma, notes = ex.chroma(
        dsp.spectrum(tone(64, 0.5)[: DEFAULT.fft_size], DEFAULT.fft_size)
    )
    assert chroma.sum() == pytest.approx(1.0)
    assert notes.sum() == pytest.approx(1.0)

    silent_chroma, silent_notes = ex.chroma(np.zeros(DEFAULT.fft_size // 2 + 1))
    assert np.all(silent_chroma == 0.0)
    assert np.all(silent_notes == 0.0)


def test_octave_energy_peaks_at_the_played_note():
    """Per-note energies must favour the actual octave, otherwise octave mode
    (decision E) could never work at all."""
    ex = ChromaExtractor(DEFAULT)
    _, notes = ex.chroma(
        dsp.spectrum(tone(60, 0.5)[: DEFAULT.fft_size], DEFAULT.fft_size)
    )
    assert int(np.argmax(notes)) == ex.note_index(60)


# --- onset -----------------------------------------------------------------


def test_onset_fires_on_a_strike():
    results = analyse(struck(tone(60, 1.0)), (0,), DEFAULT)
    assert any(r.onset for r in results)


def noisy(signal: np.ndarray, level: float = 0.0008, seed: int = 3) -> np.ndarray:
    """Add room tone. Digital silence is not a realistic lead-in and hides a
    whole class of bug — see the test immediately below."""
    rng = np.random.default_rng(seed)
    return signal + rng.normal(0.0, level, len(signal))


def test_room_noise_does_not_fire_an_onset_before_the_strike():
    """Regression: with only the absolute floor to compare against, the first
    frames reported room tone as a strike. That armed the verifier before a key
    was touched — and because the analyser used to clear onset history on every
    step change, it would have re-armed for free at every step of a song."""
    results = analyse(noisy(struck(tone(60, 2.0), lead_silence=1.0)), (0,), DEFAULT)
    onsets = [r.time_ms for r in results if r.onset]
    assert onsets, "the real strike must still register"
    assert min(onsets) > 700, (
        f"onset fired at {min(onsets):.0f} ms, before the 1 s strike"
    )


def test_changing_target_keeps_onset_history():
    """The flux history describes the audio, not the step. Resetting it on
    advance would return the detector to its warm-up state mid-song."""
    from spike.core.pipeline import Analyser
    from spike.core import dsp

    analyser = Analyser(DEFAULT, (0,))
    frames = list(
        dsp.iter_frames(noisy(struck(tone(60, 2.0))), DEFAULT.fft_size, DEFAULT.hop)
    )
    for f in frames[:10]:
        analyser.push(f)
    history_before = len(analyser.onset._history)
    analyser.set_target((5,))
    assert len(analyser.onset._history) == history_before


def test_one_strike_reports_exactly_one_onset():
    """The refractory gate collapses the multi-frame flux burst that a single
    strike produces as the 170 ms window slides over it. Without it, one note
    reports ~5 onsets and each one restarts the stability clock."""
    results = analyse(struck(tone(60, 2.0)), (0,), DEFAULT)
    onsets = [r.index for r in results if r.onset]
    assert len(onsets) == 1


def test_beating_within_a_held_chord_is_not_an_onset():
    """Equal-tempered intervals beat: in C major, C4's 3rd harmonic (784.9 Hz)
    and G4's 2nd (784.0 Hz) swell against each other at ~0.9 Hz. That swell is
    real positive spectral flux arriving in the middle of a sustained chord,
    and the adaptive median alone does not reject it — the flux floor does."""
    results = analyse(struck(chord([60, 64, 67], 4.0)), (0, 4, 7), DEFAULT)
    onsets = [r.index for r in results if r.onset]
    assert len(onsets) == 1, f"beating produced phantom strikes at frames {onsets}"


def test_steady_tone_produces_no_onset_after_the_attack():
    """Decay must not read as attack. Flux is half-wave rectified precisely so
    that a sustaining chord stops generating onsets."""
    results = analyse(struck(tone(60, 3.0)), (0,), DEFAULT)
    onsets = [r.index for r in results if r.onset]
    assert onsets, "expected the initial strike to register"
    attack = min(onsets)
    # Nothing may fire once the tone is steady, well past the refractory window.
    late = [i for i in onsets if i > attack + DEFAULT.onset_refractory_frames + 2]
    assert late == []


# --- verification ----------------------------------------------------------


def confirm_frame(samples, target, params=DEFAULT):
    """Run a signal and return the frame index of confirmation, or None."""
    results = analyse(samples, target, params)
    for r in results:
        if r.verdict and r.verdict.confirmed_now:
            return r.index
    return None


def test_single_note_confirms():
    assert confirm_frame(struck(tone(60, 1.5)), (0,)) is not None


def test_major_triad_confirms_against_its_own_pitch_classes():
    assert confirm_frame(struck(chord([60, 64, 67], 1.5)), (0, 4, 7)) is not None


def test_major_triad_rejected_against_a_different_chord():
    """C major must not satisfy an F major target (F, A, C): the F and A are
    simply not being played."""
    assert confirm_frame(struck(chord([60, 64, 67], 1.5)), (5, 9, 0)) is None


def test_silence_never_confirms():
    assert confirm_frame(np.zeros(SR * 2), (0,)) is None


def test_sustained_tone_without_onset_never_confirms():
    """The pedal case in miniature. Analysis begins mid-sustain, so there is no
    attack to arm the verifier and the step must not advance — PLAN's
    'still-ringing chord doesn't auto-satisfy the next step'."""
    assert confirm_frame(tone(60, 2.0), (0,)) is None


def test_stability_window_is_enforced():
    """A note shorter than the stability window must not confirm."""
    brief = struck(tone(60, 0.05), lead_silence=0.4)
    padded = np.concatenate([brief, np.zeros(int(0.6 * SR))])
    assert confirm_frame(padded, (0,)) is None


def test_extra_note_blocks_confirmation():
    """Target C+E while C+E+G sounds. G is unexpected and loud, so the step
    must not advance — PLAN step 5, decision A."""
    played = struck(chord([60, 64, 67], 1.5))
    assert confirm_frame(played, (0, 4)) is None


def test_harmonic_exclusion_changes_the_extras_verdict():
    """Documents the decision-A trade rather than asserting a winner.

    With exclusion off, the fifth above a target counts as a wrong note. With
    it on, that same fifth is forgiven as a possible overtone — which also
    means a genuinely wrong fifth would slip through. `sweep.py` measures which
    costs less on the real corpus.
    """
    played = struck(chord([60, 64, 67], 1.5))
    strict = DEFAULT.but(harmonic_exclusion=False)
    lenient = DEFAULT.but(harmonic_exclusion=True)
    assert confirm_frame(played, (0, 4), strict) is None
    assert confirm_frame(played, (0, 4), lenient) is not None


def test_harmonic_offsets_are_octave_fifth_and_third():
    assert harmonic_pitch_class_offsets(2) == (0,)
    assert harmonic_pitch_class_offsets(3) == (0, 7)
    assert harmonic_pitch_class_offsets(5) == (0, 4, 7)


def test_latency_is_measured_from_the_onset():
    results = analyse(struck(tone(60, 2.0), lead_silence=1.0), (0,), DEFAULT)
    verifier_latency = None
    for r in results:
        if r.verdict and r.verdict.confirmed_now:
            verifier_latency = r.index
            break
    assert verifier_latency is not None
    # Confirmation happens ~1 s in (the lead silence), but latency is counted
    # from the strike, so it must be close to the stability window itself.
    assert verifier_latency * DEFAULT.frame_interval_ms > 800


# --- params ----------------------------------------------------------------


def test_stability_frames_derivation():
    """Assert the relationships rather than the literals, so retuning a
    constant does not fail this test for the wrong reason — but a broken
    derivation still does."""
    assert DEFAULT.frame_interval_ms == pytest.approx(
        1000 * DEFAULT.hop / DEFAULT.sample_rate
    )
    assert DEFAULT.stability_frames == math.ceil(
        DEFAULT.stability_ms / DEFAULT.frame_interval_ms
    )


def test_latency_budget_is_within_the_documented_limit():
    """DECISIONS #3 budgets 300-700 ms to confirm. The floor is the stability
    window plus one frame of quantisation plus the time for the attack to fill
    enough of the window to be detected."""
    floor_ms = DEFAULT.stability_ms + DEFAULT.frame_interval_ms
    assert floor_ms < 700, f"cannot confirm inside the budget: {floor_ms:.0f} ms"


def test_onset_median_window_is_about_nine_tenths_of_a_second():
    """ONSET_WINDOW_FRAMES is expressed in frames but exists to span a fixed
    wall-clock period. Halving the hop without doubling it would silently cut
    the adaptive median's history in half, which is exactly the kind of coupling
    that breaks quietly."""
    span_ms = DEFAULT.onset_window_frames * DEFAULT.frame_interval_ms
    assert 700 < span_ms < 1200, f"adaptive median spans {span_ms:.0f} ms"


def test_bin_width_matches_the_documented_resolution_risk():
    """5.86 Hz bins vs ~3.9 Hz semitone spacing at C2 — the bass problem is
    arithmetic, not opinion, so it gets pinned here."""
    assert DEFAULT.bin_hz == pytest.approx(5.859, abs=1e-3)
    c2 = float(midi_to_hz(36))
    csharp2 = float(midi_to_hz(37))
    assert (csharp2 - c2) < DEFAULT.bin_hz


def test_params_but_is_a_copy():
    p = Params()
    q = p.but(present_thresh=0.9)
    assert p.present_thresh != q.present_thresh
    assert q.fft_size == p.fft_size
