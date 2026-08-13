"""Grid-search the operating point — turns three open decisions into numbers.

    uv run python -m spike.sweep
    uv run python -m spike.sweep --fft 8192,16384 --quick

DECISIONS.md leaves A (extra-note strictness), B (stability window) and E
(octave vs pitch class) open with a "decide during build" note and a
recommendation each. This is how they get decided: run the corpus at every
combination and read off which one the recording actually supports.

The scoring rule ranks false-confirm avoidance above recall on purpose. A
missed note is a player pressing a key again; a false confirm is the song
advancing past a note that was never played, which cascades and cannot be
recovered from inside wait-mode.
"""

from __future__ import annotations

import argparse
import itertools
import json
from dataclasses import dataclass
from pathlib import Path

from . import audioio
from .core.pipeline import precompute, replay
from .params import DEFAULT, Params
from .record import CORPUS_DIR
from .score import (
    applicable,
    FALSE_CONFIRM_BAR,
    LATENCY_BAR_MS,
    RECALL_BAR,
    load_corpus,
    target_for,
)

BOLD = "\x1b[1m"
DIM = "\x1b[2m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
RED = "\x1b[31m"
RESET = "\x1b[0m"

PRESENT_GRID = (0.20, 0.25, 0.30, 0.35, 0.40, 0.45)
EXTRA_GRID = (0.40, 0.50, 0.60, 0.70, 0.85)
STABILITY_GRID = (150.0, 200.0, 250.0, 300.0, 400.0)
FFT_GRID = (8192, 16384)

QUICK_PRESENT = (0.25, 0.35, 0.45)
QUICK_EXTRA = (0.50, 0.70)
QUICK_STABILITY = (200.0, 300.0)


@dataclass
class Point:
    params: Params
    recall: float
    false_confirm: float
    median_latency: float | None
    by_register: dict[str, float]

    @property
    def clears_all(self) -> bool:
        return (
            self.recall >= RECALL_BAR
            and self.false_confirm <= FALSE_CONFIRM_BAR
            and self.median_latency is not None
            and self.median_latency <= LATENCY_BAR_MS
        )

    @property
    def rank_key(self) -> tuple:
        """Sort key: false confirms dominate, then recall, then latency.

        Not a weighted sum — a weighted sum lets a high recall buy off a false
        confirm, and in wait-mode it cannot. A configuration that advances the
        song on notes the player never struck is unusable no matter how well it
        detects the ones they did.
        """
        return (
            self.false_confirm,
            -self.recall,
            self.median_latency if self.median_latency is not None else 1e9,
        )

    def label(self) -> str:
        p = self.params
        mode = "octave" if p.octave_mode else "pc"
        excl = "+excl" if p.harmonic_exclusion else ""
        return (
            f"fft{p.fft_size} {mode}{excl} present{p.present_thresh:.2f} "
            f"extra{p.extra_margin:.2f} stab{p.stability_ms:.0f}ms"
        )


def evaluate(cache, entries, params: Params) -> Point:
    """Replay the verifier across the whole corpus at one operating point."""
    pos_total = pos_hit = neg_total = neg_hit = 0
    latencies: list[float] = []
    per_reg: dict[str, list[int]] = {}

    for entry in entries:
        if not applicable(entry, params):
            continue
        data = cache[(entry["stem"], params.fft_size)]
        result = replay(data, target_for(entry, params), params)

        if entry["kind"] == "positive":
            pos_total += 1
            bucket = per_reg.setdefault(entry["register"], [0, 0])
            bucket[1] += 1
            if result.confirmed:
                pos_hit += 1
                bucket[0] += 1
                if result.latency_ms is not None:
                    latencies.append(result.latency_ms)
        else:
            neg_total += 1
            if result.confirmed:
                neg_hit += 1

    latencies.sort()
    median = latencies[len(latencies) // 2] if latencies else None

    return Point(
        params=params,
        recall=pos_hit / pos_total if pos_total else 0.0,
        false_confirm=neg_hit / neg_total if neg_total else 0.0,
        median_latency=median,
        by_register={k: (v[0] / v[1] if v[1] else 0.0) for k, v in per_reg.items()},
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="spike.sweep",
        description="Grid-search verification thresholds against the corpus.",
    )
    ap.add_argument("--fft", default=None, help="comma-separated FFT sizes")
    ap.add_argument("--quick", action="store_true", help="coarse grid, ~10x faster")
    ap.add_argument("--top", type=int, default=12, help="how many points to print")
    ap.add_argument("--json", type=Path, default=None, help="write all points as JSON")
    ap.add_argument("--corpus", type=Path, default=CORPUS_DIR, help="corpus directory")
    args = ap.parse_args(argv)

    entries = load_corpus(args.corpus)

    fft_sizes = (
        tuple(int(s) for s in args.fft.split(",")) if args.fft else FFT_GRID
    )
    present = QUICK_PRESENT if args.quick else PRESENT_GRID
    extra = QUICK_EXTRA if args.quick else EXTRA_GRID
    stability = QUICK_STABILITY if args.quick else STABILITY_GRID

    combos = list(
        itertools.product(fft_sizes, present, extra, stability, (False, True), (False, True))
    )
    print(f"{BOLD}piano-coach — threshold sweep{RESET}")
    print(f"  {len(entries)} takes × {len(combos)} operating points")
    print(f"{DIM}  precomputing chroma and onsets per FFT size…{RESET}")

    # One FFT pass per (take, fft_size); every threshold combination replays
    # against these cached frames.
    cache = {}
    for size in fft_sizes:
        front_end = DEFAULT.but(fft_size=size)
        for entry in entries:
            samples, _ = audioio.read_wav(args.corpus / entry["file"])
            cache[(entry["stem"], size)] = precompute(samples, front_end)
        print(f"{DIM}    fft {size}: {len(entries)} takes cached{RESET}")

    points: list[Point] = []
    for size, pres, ext, stab, octave, excl in combos:
        params = DEFAULT.but(
            fft_size=size,
            present_thresh=pres,
            extra_margin=ext,
            stability_ms=stab,
            octave_mode=octave,
            harmonic_exclusion=excl,
        )
        points.append(evaluate(cache, entries, params))

    points.sort(key=lambda p: p.rank_key)

    print()
    print(f"{BOLD}Best operating points{RESET} {DIM}(false confirms first, then recall){RESET}")
    print(
        f"{DIM}  {'configuration':56s} {'recall':>7s} {'false':>7s} {'latency':>8s}{RESET}"
    )
    for p in points[: args.top]:
        colour = GREEN if p.clears_all else (YELLOW if p.false_confirm <= FALSE_CONFIRM_BAR else RED)
        lat = f"{p.median_latency:.0f}ms" if p.median_latency is not None else "—"
        print(
            f"  {colour}{p.label():56s}{RESET} {p.recall * 100:6.1f}% "
            f"{p.false_confirm * 100:6.1f}% {lat:>8s}"
        )

    clearing = [p for p in points if p.clears_all]
    print()
    if clearing:
        best = clearing[0]
        print(f"{GREEN}{BOLD}{len(clearing)} configuration(s) clear all three bars.{RESET}")
        print(f"  Best: {BOLD}{best.label()}{RESET}")
        print()
        print("  Copy into spike/params.py:")
        print(f"    FFT_SIZE = {best.params.fft_size}")
        print(f"    PRESENT_THRESH = {best.params.present_thresh}")
        print(f"    EXTRA_MARGIN = {best.params.extra_margin}")
        print(f"    STABILITY_MS = {best.params.stability_ms:g}")
        print(f"    OCTAVE_MODE = {best.params.octave_mode}")
        print(f"    HARMONIC_EXCLUSION = {best.params.harmonic_exclusion}")
    else:
        best = points[0]
        print(f"{YELLOW}{BOLD}No configuration clears all three bars.{RESET}")
        print(f"  Closest: {BOLD}{best.label()}{RESET}")
        print(
            f"  recall {best.recall * 100:.1f}%  false {best.false_confirm * 100:.1f}%  "
            f"latency {best.median_latency:.0f}ms" if best.median_latency
            else f"  recall {best.recall * 100:.1f}%  false {best.false_confirm * 100:.1f}%"
        )
        print()
        print("  Per-register recall at that point — a weak register here is the")
        print("  difference between CONSTRAINED GO and NO-GO:")
        for reg, r in sorted(best.by_register.items()):
            mark = GREEN if r >= RECALL_BAR else RED
            print(f"    {reg:8s} {mark}{r * 100:5.1f}%{RESET}")

    if args.json:
        args.json.write_text(
            json.dumps(
                [
                    {
                        "fft_size": p.params.fft_size,
                        "present_thresh": p.params.present_thresh,
                        "extra_margin": p.params.extra_margin,
                        "stability_ms": p.params.stability_ms,
                        "octave_mode": p.params.octave_mode,
                        "harmonic_exclusion": p.params.harmonic_exclusion,
                        "recall": p.recall,
                        "false_confirm": p.false_confirm,
                        "median_latency_ms": p.median_latency,
                        "by_register": p.by_register,
                    }
                    for p in points
                ],
                indent=2,
            )
            + "\n"
        )
        print(f"\n{DIM}wrote {args.json}{RESET}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
