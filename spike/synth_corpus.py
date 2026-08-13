"""Generate a fake corpus from synthesised tones.

    uv run python -m spike.synth_corpus /tmp/fake-corpus

Not a substitute for the real recording — synthetic partials are cleaner than
any piano through any microphone, and a GO on this proves nothing about
hardware. Its job is to exercise `score`, `sweep` and `plot` end to end so that
the first time they run against real audio is not the first time they run at
all.

It is also useful as a sanity reference while tuning: if a threshold change
breaks the synthetic corpus, it is broken in the algorithm rather than in the
room.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np

from . import audioio
from .corpus_script import SCRIPT, plan
from .params import DEFAULT, Params

PARTIALS = (1.0, 0.5, 0.28, 0.16, 0.09, 0.05)
DYNAMIC_AMP = {"soft": 0.06, "medium": 0.22, "loud": 0.55}


def piano_note(
    midi: int, seconds: float, params: Params, amp: float, decay: float = 1.6
) -> np.ndarray:
    """A crude struck-string model: harmonic stack with an exponential decay
    and a fast attack. Enough structure to drive the chroma extractor
    realistically; nowhere near enough to stand in for a real instrument."""
    n = int(seconds * params.sample_rate)
    t = np.arange(n, dtype=np.float64) / params.sample_rate
    f0 = 440.0 * 2.0 ** ((midi - 69) / 12.0)

    sig = np.zeros(n)
    for h, weight in enumerate(PARTIALS, start=1):
        f = f0 * h
        if f >= params.sample_rate / 2:
            break
        # Upper partials decay faster, as they do on a real string.
        sig += weight * np.exp(-decay * h**0.5 * t) * np.sin(2 * np.pi * f * t + 0.7 * h)

    attack = np.clip(t / 0.006, 0.0, 1.0)
    sig *= attack
    peak = np.max(np.abs(sig))
    return amp * sig / peak if peak > 0 else sig


def render(entry_item, dynamic: str, params: Params, seconds: float, rng) -> np.ndarray:
    """Build one take: lead-in silence, the strike(s), then tail."""
    amp = DYNAMIC_AMP[dynamic]
    total = int(seconds * params.sample_rate)
    out = np.zeros(total)

    lead = int(0.6 * params.sample_rate)

    if entry_item.id == "pedal_c_then_f":
        # C major first, then F major a second later, both left ringing —
        # the overlap is the entire point of the item.
        for m in (60, 64, 67):
            note = piano_note(m, seconds - 0.6, params, amp, decay=0.5)
            out[lead : lead + len(note)] += note
        second = lead + int(1.0 * params.sample_rate)
        for m in entry_item.played:
            note = piano_note(m, seconds - 1.6, params, amp, decay=0.5)
            out[second : second + len(note)] += note
    elif entry_item.id == "pedal_ring_only":
        for m in entry_item.played:
            note = piano_note(m, seconds - 0.6, params, amp, decay=0.4)
            out[lead : lead + len(note)] += note
    else:
        for m in entry_item.played:
            note = piano_note(m, seconds - 0.6, params, amp)
            out[lead : lead + len(note)] += note

    # Room noise, so the silence gate and adaptive onset threshold see
    # something other than mathematical zero.
    out += rng.normal(0.0, 0.0008, total)

    peak = np.max(np.abs(out))
    if peak > 0.95:
        out *= 0.95 / peak
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="spike.synth_corpus",
        description="Write a synthetic corpus for exercising the analysis tools.",
    )
    ap.add_argument("out", type=Path, help="output directory")
    ap.add_argument("--seconds", type=float, default=4.0)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args(argv)

    params = DEFAULT
    rng = np.random.default_rng(args.seed)
    args.out.mkdir(parents=True, exist_ok=True)

    entries = []
    for p in plan(SCRIPT):
        samples = render(p.item, p.dynamic, params, args.seconds, rng)
        path = args.out / f"{p.stem}.wav"
        audioio.write_wav(path, samples, params.sample_rate)
        entries.append(
            {
                "stem": p.stem,
                "file": path.name,
                "item": p.item.id,
                "name": p.item.name,
                "kind": p.item.kind,
                "register": p.item.register,
                "played": list(p.item.played),
                "target": list(p.item.target),
                "modes": list(p.item.modes),
                "pedal": p.item.pedal,
                "dynamic": p.dynamic,
                "take": p.take,
                "sample_rate": params.sample_rate,
                "seconds": args.seconds,
                "device": "SYNTHETIC — not a real recording",
                "peak": float(np.max(np.abs(samples))),
                "rms": float(np.sqrt(np.mean(samples**2))),
                "clipped": 0.0,
                "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "synthetic": True,
            }
        )

    (args.out / "manifest.json").write_text(json.dumps(entries, indent=2) + "\n")
    print(f"wrote {len(entries)} synthetic takes to {args.out}")
    print(f"score them:  uv run python -m spike.score --corpus {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
