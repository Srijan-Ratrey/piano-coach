"""Emit golden vectors — the acceptance test for the future JavaScript port.

    uv run python -m spike.export_golden
    uv run python -m spike.export_golden --corpus spike/corpus

This file is why the "spike in Python, app in JS" split is safe (DECISION #16).
Tuned thresholds alone do not transfer: they are only meaningful alongside the
exact transform that produced them, and a JS reimplementation that differs in
window convention, bin rounding or normalisation will read the same constants
and behave differently. A spike whose numbers do not survive the port measured
nothing.

So the contract is expressed as data rather than prose, in two independent
layers:

  frames         embedded audio -> expected chroma, note energies, RMS.
                 Catches DSP drift: windowing, FFT scaling, band tables,
                 harmonic weights, normalisation.

  verifier_trace synthetic chroma vectors -> expected verdicts, no audio at
                 all. Catches state-machine drift: thresholds, arming,
                 stability counting, extras. Portable to any language with no
                 FFT involved.

Splitting them matters: when the port fails, one layer says "your FFT is wrong"
and the other says "your logic is wrong", instead of a single opaque mismatch.
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

import numpy as np

from . import audioio
from .core import dsp
from .core.chroma import ChromaExtractor, midi_to_hz, note_name
from .core.onset import OnsetDetector
from .core.verify import Verifier
from .params import DEFAULT, Params
from .record import CORPUS_DIR

GOLDEN_DIR = Path(__file__).parent / "golden"
ROUND = 9


def _b64_int16(samples: np.ndarray) -> str:
    """Samples as base64 little-endian int16 — the corpus's own format, so the
    round trip is exact rather than approximate."""
    pcm = np.round(np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
    return base64.b64encode(pcm.tobytes()).decode("ascii")


def _synthetic_signal(
    midis: tuple[int, ...], n: int, params: Params, amp: float = 0.3
) -> np.ndarray:
    """A deterministic steady tone of `n` samples.

    Deterministic so golden vectors regenerate identically on any machine, with
    or without a corpus. Generated as one continuous waveform rather than a
    repeated block — tiling an identical frame introduces a phase discontinuity
    at every seam, and each seam registers as a spectral-flux spike, i.e. a
    phantom strike.
    """
    t = np.arange(n, dtype=np.float64) / params.sample_rate
    sig = np.zeros(n)
    for m in midis:
        f0 = float(midi_to_hz(m))
        for h, w in enumerate((1.0, 0.5, 0.28, 0.16), start=1):
            if f0 * h < params.sample_rate / 2:
                sig += w * np.sin(2 * np.pi * f0 * h * t + 0.7 * h + 0.11 * m)
    peak = np.max(np.abs(sig))
    return amp * sig / peak if peak else sig


def _synthetic_frame(midis: tuple[int, ...], params: Params, amp: float = 0.3) -> np.ndarray:
    return _synthetic_signal(midis, params.fft_size, params, amp)


def frame_case(name: str, samples: np.ndarray, params: Params) -> dict:
    extractor = ChromaExtractor(params)
    spec = dsp.spectrum(samples, params.fft_size)
    chroma, notes = extractor.chroma(spec)

    top = sorted(range(len(notes)), key=lambda i: -notes[i])[:6]
    return {
        "id": name,
        "samples_b64_int16": _b64_int16(samples),
        "expected": {
            "rms": round(dsp.rms(samples), ROUND),
            "chroma": [round(float(v), ROUND) for v in chroma],
            "top_notes": [
                [note_name(params.midi_low + i), round(float(notes[i]), ROUND)] for i in top
            ],
        },
    }


def verifier_trace(params: Params) -> dict:
    """A hand-built chroma sequence exercising every branch of the verifier.

    No audio: the point is to pin the *logic* independently of the transform,
    so a port can validate its state machine before its FFT works.
    """
    def c(**pcs) -> list[float]:
        v = [0.01] * 12
        for name, value in pcs.items():
            v[{"C": 0, "E": 4, "G": 7, "A": 9, "F": 5, "B": 11}[name]] = value
        return v

    steps = [
        # (chroma, onset, rms, comment)
        (c(C=0.01), False, 0.0001, "silence — below the RMS gate"),
        (c(C=0.01), False, 0.0001, "still silent"),
        (c(C=0.9, E=0.8, G=0.7), False, 0.2, "chord present but no onset seen yet"),
        (c(C=0.9, E=0.8, G=0.7), True, 0.2, "onset — arms the verifier, hold starts"),
        (c(C=0.9, E=0.8, G=0.7), False, 0.2, "holding 2"),
        (c(C=0.9, E=0.8, G=0.7), False, 0.2, "holding 3"),
        (c(C=0.9, E=0.8, G=0.7, B=0.8), False, 0.2, "extra B appears — hold resets"),
        (c(C=0.9, E=0.8, G=0.7), False, 0.2, "extra gone — hold restarts at 1"),
        (c(C=0.9, E=0.8, G=0.7), False, 0.2, "holding 2"),
        (c(C=0.9, E=0.8, G=0.7), False, 0.2, "holding 3"),
        (c(C=0.9, E=0.8, G=0.7), False, 0.2, "holding 4"),
        (c(C=0.9, E=0.8, G=0.7), False, 0.2, "holding 5"),
        (c(C=0.9, E=0.8, G=0.7), False, 0.2, "holding 6 — confirms here at default stability"),
        (c(C=0.9, E=0.8, G=0.7), False, 0.2, "latched"),
        (c(C=0.2), False, 0.2, "chord released — stays latched until reset"),
    ]

    verifier = Verifier((0, 4, 7), params)
    n_notes = params.midi_high - params.midi_low + 1
    out = []
    for chroma, onset, rms, comment in steps:
        v = verifier.step(np.array(chroma), np.zeros(n_notes), onset, rms)
        out.append(
            {
                "comment": comment,
                "input": {"chroma": chroma, "onset": onset, "rms": rms},
                "expected": {
                    "armed": v.armed,
                    "targets_present": v.targets_present,
                    "extras": list(v.extras),
                    "frames_held": v.frames_held,
                    "confirmed_now": v.confirmed_now,
                    "latched": v.latched,
                },
            }
        )

    return {
        "target_pitch_classes": [0, 4, 7],
        "stability_frames": params.stability_frames,
        "steps": out,
    }


def build(params: Params, corpus: Path | None) -> dict:
    extractor = ChromaExtractor(params)

    frames: list[dict] = []
    for name, midis in (
        ("synthetic_C4", (60,)),
        ("synthetic_C2", (36,)),
        ("synthetic_C_major", (60, 64, 67)),
        ("synthetic_G7", (55, 59, 62, 65)),
    ):
        frames.append(frame_case(name, _synthetic_frame(midis, params), params))

    if corpus is not None and (corpus / "manifest.json").exists():
        entries = json.loads((corpus / "manifest.json").read_text())
        # One real frame from the middle of a sustained take, if available. A
        # real frame is the only thing that exercises the port against actual
        # instrument spectra rather than clean sine stacks.
        for wanted in ("c4_single", "c_major"):
            entry = next((e for e in entries if e["item"] == wanted), None)
            if entry is None:
                continue
            samples, _ = audioio.read_wav(corpus / entry["file"])
            start = min(len(samples) - params.fft_size, int(1.2 * params.sample_rate))
            if start < 0:
                continue
            frames.append(
                frame_case(
                    f"corpus_{entry['stem']}_frame",
                    samples[start : start + params.fft_size],
                    params,
                )
            )

    # A short onset sequence, to pin spectral flux and the refractory gate.
    detector = OnsetDetector(params)
    lead = np.zeros(params.fft_size * 2)
    body = _synthetic_signal((60, 64, 67), params.fft_size * 6, params)
    signal = np.concatenate([lead, body])
    onset_flags = []
    for f in dsp.iter_frames(signal, params.fft_size, params.hop):
        onset_flags.append(bool(detector.step(dsp.spectrum(f, params.fft_size))))

    return {
        "_comment": (
            "Acceptance vectors for a reimplementation of spike/core in another "
            "language. See spike/ALGORITHM.md for the maths. Every number here was "
            "produced by the Python reference; a port is correct when it reproduces "
            "them to the stated tolerance."
        ),
        "tolerance": 1e-6,
        "params": {
            k: (list(v) if isinstance(v, tuple) else v) for k, v in params.__dict__.items()
        },
        "derived": {
            "bin_hz": round(params.bin_hz, ROUND),
            "frame_interval_ms": round(params.frame_interval_ms, ROUND),
            "stability_frames": params.stability_frames,
            "window_smear_frames": params.window_smear_frames,
            "onset_refractory_frames": params.onset_refractory_frames,
        },
        "checks": {
            "hann_periodic_8": [round(float(v), ROUND) for v in dsp.hann_periodic(8)],
            "midi_to_hz": {
                str(m): round(float(midi_to_hz(m)), 6) for m in (36, 48, 60, 69, 84, 96)
            },
            "bands": [
                {
                    "midi": params.midi_low + i,
                    "note": note_name(params.midi_low + i),
                    "harmonic": h,
                    "lo_bin": lo,
                    "hi_bin_exclusive": hi,
                }
                for i, h, lo, hi in _band_rows(extractor)
            ],
        },
        "frames": frames,
        "onset_sequence": {
            "description": (
                "Two frames of digital silence then a repeated C major frame. "
                "Exactly one onset is expected, after the warm-up window, and the "
                "refractory gate must suppress the rest of the attack burst."
            ),
            "expected_onset_frames": [i for i, f in enumerate(onset_flags) if f],
            "total_frames": len(onset_flags),
        },
        "verifier_trace": verifier_trace(params),
    }


def _band_rows(extractor: ChromaExtractor):
    """A sample of the band table — enough to catch an off-by-one without
    embedding all 244 rows."""
    sample_notes = {36, 48, 60, 72, 84, 96}
    seen: dict[tuple[int, int], int] = {}
    rows = []
    for note_i, weight, lo, hi in extractor.bands:
        midi = int(extractor.notes[note_i])
        if midi not in sample_notes:
            continue
        h = seen.get((note_i, 0), 0) + 1
        seen[(note_i, 0)] = h
        rows.append((note_i, h, lo, hi))
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="spike.export_golden",
        description="Write acceptance vectors for the JS port.",
    )
    ap.add_argument("--out", type=Path, default=GOLDEN_DIR / "golden.json")
    ap.add_argument(
        "--corpus",
        type=Path,
        default=CORPUS_DIR,
        help="include a real frame from this corpus, if it exists",
    )
    args = ap.parse_args(argv)

    data = build(DEFAULT, args.corpus)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=2) + "\n")

    size_kb = args.out.stat().st_size / 1024
    print(f"wrote {args.out} ({size_kb:.0f} KB)")
    print(f"  {len(data['frames'])} frame vectors")
    print(f"  {len(data['verifier_trace']['steps'])} verifier trace steps")
    print(f"  onsets at frames {data['onset_sequence']['expected_onset_frames']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
