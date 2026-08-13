"""Score the recorded corpus — this is the go/no-go measurement.

    uv run python -m spike.score
    uv run python -m spike.score --octave
    uv run python -m spike.score --json results.json

Runs `core/` over every take in the manifest and reports:

  * per-take: confirmed or not, onset-to-confirm latency, blocking extras
  * recall on positives, false-confirm rate on negatives, median latency
  * a per-register breakdown, which is what makes CONSTRAINED GO available as
    an answer instead of collapsing a partial success into a flat NO-GO

The three bars come from the project's own documents, not from this file:
PLAN sets recall at ~90%, DECISIONS #3 sets the latency budget at 300-700 ms,
and the false-confirm bar follows from PLAN's warning that without extra-note
rejection "overtones/neighbours make everything read correct".
"""

from __future__ import annotations

import argparse
import json
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path

from . import audioio
from .core.chroma import PITCH_CLASS_NAMES, note_name
from .core.pipeline import analyse
from .params import DEFAULT, Params
from .record import CORPUS_DIR, MANIFEST

BOLD = "\x1b[1m"
DIM = "\x1b[2m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
RED = "\x1b[31m"
RESET = "\x1b[0m"

RECALL_BAR = 0.90
FALSE_CONFIRM_BAR = 0.05
LATENCY_BAR_MS = 700.0


@dataclass
class TakeResult:
    stem: str
    item: str
    kind: str
    register: str
    dynamic: str
    target: list[int]
    confirmed: bool
    correct: bool
    """Positives are correct when confirmed; negatives when not confirmed."""
    latency_ms: float | None
    onset_frame: int | None
    frames: int
    blocking_extras: list[str]
    """Pitch classes (or notes) that most often blocked confirmation. Empty for
    takes that confirmed — the interesting case is a positive that did not."""
    peak_hold: int
    """Longest run of frames where all conditions held. Distinguishes 'never
    close' from 'nearly confirmed', which point at different fixes."""


def applicable(entry: dict, params: Params) -> bool:
    """Whether this take asks a meaningful question in the current mode.

    See `Item.modes`: a C4+C5 target folds to a single pitch class, so items
    built around octave discrimination are scored only in octave mode. Scoring
    them regardless would charge the detector with false confirms for correct
    behaviour — which is exactly what happened on the first synthetic run,
    where three tautological takes alone pushed the false-confirm rate to 25%
    and produced a spurious NO-GO.
    """
    modes = entry.get("modes") or ("pitch_class", "octave")
    return ("octave" if params.octave_mode else "pitch_class") in modes


def target_for(entry: dict, params: Params) -> tuple[int, ...]:
    """Manifest targets are MIDI notes; fold to pitch classes unless in octave
    mode. Folding here rather than at record time means one corpus serves both
    sides of decision E."""
    midi = tuple(entry["target"])
    if params.octave_mode:
        return midi
    return tuple(sorted({m % 12 for m in midi}))


def score_take(entry: dict, params: Params, corpus_dir: Path) -> TakeResult:
    samples, rate = audioio.read_wav(corpus_dir / entry["file"])
    if rate != params.sample_rate:
        raise ValueError(
            f"{entry['file']} is {rate} Hz but params expect {params.sample_rate} Hz"
        )

    target = target_for(entry, params)
    results = analyse(samples, target, params)

    confirmed = False
    latency = None
    onset_frame = None
    peak_hold = 0
    extra_counts: dict[int, int] = {}

    for r in results:
        v = r.verdict
        if v is None:
            continue
        peak_hold = max(peak_hold, v.frames_held)
        if v.onset and onset_frame is None:
            onset_frame = v.frame
        for e in v.extras:
            extra_counts[e] = extra_counts.get(e, 0) + 1
        if v.confirmed_now:
            confirmed = True
            if onset_frame is not None:
                latency = (v.frame - onset_frame) * params.frame_interval_ms

    ranked = sorted(extra_counts.items(), key=lambda kv: -kv[1])[:4]
    labels = [
        (note_name(e) if params.octave_mode else PITCH_CLASS_NAMES[e % 12])
        + f"×{n}"
        for e, n in ranked
    ]

    is_positive = entry["kind"] == "positive"
    return TakeResult(
        stem=entry["stem"],
        item=entry["item"],
        kind=entry["kind"],
        register=entry["register"],
        dynamic=entry["dynamic"],
        target=list(target),
        confirmed=confirmed,
        correct=(confirmed if is_positive else not confirmed),
        latency_ms=latency,
        onset_frame=onset_frame,
        frames=len(results),
        blocking_extras=labels,
        peak_hold=peak_hold,
    )


@dataclass
class Summary:
    positives: int
    positives_confirmed: int
    negatives: int
    negatives_confirmed: int
    latencies: list[float]

    @property
    def recall(self) -> float:
        return self.positives_confirmed / self.positives if self.positives else 0.0

    @property
    def false_confirm_rate(self) -> float:
        return self.negatives_confirmed / self.negatives if self.negatives else 0.0

    @property
    def median_latency(self) -> float | None:
        return statistics.median(self.latencies) if self.latencies else None


def summarise(results: list[TakeResult]) -> Summary:
    pos = [r for r in results if r.kind == "positive"]
    neg = [r for r in results if r.kind == "negative"]
    return Summary(
        positives=len(pos),
        positives_confirmed=sum(1 for r in pos if r.confirmed),
        negatives=len(neg),
        negatives_confirmed=sum(1 for r in neg if r.confirmed),
        latencies=[r.latency_ms for r in pos if r.confirmed and r.latency_ms is not None],
    )


def verdict_line(summary: Summary, by_register: dict[str, Summary]) -> tuple[str, str]:
    """Return `(verdict, explanation)` against the pre-committed bars."""
    recall_ok = summary.recall >= RECALL_BAR
    fc_ok = summary.false_confirm_rate <= FALSE_CONFIRM_BAR
    lat = summary.median_latency
    lat_ok = lat is not None and lat <= LATENCY_BAR_MS

    if recall_ok and fc_ok and lat_ok:
        return "GO", "All three bars cleared. Proceed to PLAN step 3 with these constants."

    if not fc_ok:
        return (
            "NO-GO",
            "False confirms are the disqualifying failure: a verifier that accepts "
            "wrong notes makes wait-mode advance on its own, which is worse than no "
            "detection at all. Tighten extra-note rejection before anything else.",
        )

    strong = [name for name, s in by_register.items() if s.positives and s.recall >= RECALL_BAR]
    weak = [name for name, s in by_register.items() if s.positives and s.recall < RECALL_BAR]
    if recall_ok is False and strong and weak and fc_ok:
        return (
            "CONSTRAINED GO",
            f"Registers {', '.join(strong)} clear the bar; {', '.join(weak)} do not. "
            f"Ship with a stated range and filter songs to it — PLAN's 'learned the "
            f"ceiling cheaply' outcome, not a failure.",
        )

    if not lat_ok and recall_ok:
        return (
            "CONSTRAINED GO",
            f"Detection is accurate but slow (median {lat:.0f} ms vs {LATENCY_BAR_MS:.0f} ms). "
            "Try a shorter stability window before concluding anything about the audio.",
        )

    return (
        "NO-GO",
        "Recall is below the bar across the board. Before abandoning the mic path, "
        "check the corpus for clipping and confirm the piano's reverb was off — both "
        "depress recall in ways that look like a detection ceiling.",
    )


def report(results: list[TakeResult], params: Params) -> str:
    lines: list[str] = []
    mode = "octave (exact MIDI notes)" if params.octave_mode else "pitch class"
    lines.append(f"{BOLD}piano-coach — corpus score{RESET}")
    lines.append(
        f"  mode {mode}   fft {params.fft_size}   hop {params.hop}   "
        f"present {params.present_thresh}   extra {params.extra_margin}   "
        f"stability {params.stability_ms:g} ms ({params.stability_frames} frames)"
    )
    if params.harmonic_exclusion:
        lines.append(f"  {YELLOW}harmonic exclusion ON{RESET}")
    lines.append("")

    header = (
        f"  {'take':38s} {'kind':4s} {'reg':6s} {'conf':5s} "
        f"{'latency':>8s} {'hold':>5s}  blocking extras"
    )
    lines.append(f"{DIM}{header}{RESET}")

    for r in sorted(results, key=lambda r: (r.kind != "positive", r.item, r.dynamic)):
        mark = f"{GREEN}ok {RESET}" if r.correct else f"{RED}FAIL{RESET}"
        conf = "yes" if r.confirmed else "no"
        lat = f"{r.latency_ms:6.0f}ms" if r.latency_ms is not None else "     — "
        extras = " ".join(r.blocking_extras) if r.blocking_extras else ""
        kind = "pos" if r.kind == "positive" else "NEG"
        lines.append(
            f"  {r.stem:38s} {kind:4s} {r.register:6s} {conf:5s} "
            f"{lat:>8s} {r.peak_hold:5d}  {DIM}{extras}{RESET} {mark}"
        )

    overall = summarise(results)
    by_register = {
        reg: summarise([r for r in results if r.register == reg])
        for reg in sorted({r.register for r in results})
    }

    lines.append("")
    lines.append(f"{BOLD}Aggregate{RESET}")

    def bar_line(label: str, value: str, ok: bool, bar: str) -> str:
        colour = GREEN if ok else RED
        return f"  {label:26s} {colour}{value:>8s}{RESET}   {DIM}bar {bar}{RESET}"

    lines.append(
        bar_line(
            f"recall ({overall.positives_confirmed}/{overall.positives} positives)",
            f"{overall.recall * 100:.1f}%",
            overall.recall >= RECALL_BAR,
            f"≥ {RECALL_BAR * 100:.0f}%",
        )
    )
    lines.append(
        bar_line(
            f"false confirm ({overall.negatives_confirmed}/{overall.negatives} negatives)",
            f"{overall.false_confirm_rate * 100:.1f}%",
            overall.false_confirm_rate <= FALSE_CONFIRM_BAR,
            f"≤ {FALSE_CONFIRM_BAR * 100:.0f}%",
        )
    )
    med = overall.median_latency
    lines.append(
        bar_line(
            "median latency",
            f"{med:.0f}ms" if med is not None else "—",
            med is not None and med <= LATENCY_BAR_MS,
            f"≤ {LATENCY_BAR_MS:.0f}ms",
        )
    )

    lines.append("")
    lines.append(f"{BOLD}By register (positives){RESET}")
    for reg, s in by_register.items():
        if not s.positives:
            continue
        colour = GREEN if s.recall >= RECALL_BAR else RED
        lines.append(
            f"  {reg:8s} {colour}{s.recall * 100:5.1f}%{RESET} "
            f"{DIM}({s.positives_confirmed}/{s.positives}){RESET}"
        )

    verdict, why = verdict_line(overall, by_register)
    colour = {"GO": GREEN, "CONSTRAINED GO": YELLOW, "NO-GO": RED}[verdict]
    lines.append("")
    lines.append(f"{BOLD}Verdict:{RESET} {colour}{BOLD}{verdict}{RESET}")
    lines.append(f"  {why}")
    lines.append("")
    lines.append(
        f"{DIM}This is one operating point. Run `python -m spike.sweep` before "
        f"believing it — a NO-GO at default thresholds may be a GO at tuned ones.{RESET}"
    )
    return "\n".join(lines)


def load_corpus(corpus_dir: Path = CORPUS_DIR) -> list[dict]:
    """Read a corpus manifest. `corpus_dir` is overridable so a second corpus —
    a different piano, a different room — can be scored without disturbing the
    first, which matters given DECISIONS' warning that feasibility is
    hardware-dependent."""
    manifest = corpus_dir / MANIFEST.name
    if not manifest.exists():
        raise SystemExit(
            f"No corpus found at {manifest}.\n"
            f"Record one first:  uv run python -m spike.record"
        )
    entries = json.loads(manifest.read_text())
    if not entries:
        raise SystemExit(f"{manifest} is empty — nothing to score.")
    return entries


def build_params(args) -> Params:
    p = DEFAULT.but(octave_mode=args.octave, harmonic_exclusion=args.harmonic_exclusion)
    if args.fft:
        p = p.but(fft_size=args.fft)
    if args.present is not None:
        p = p.but(present_thresh=args.present)
    if args.extra is not None:
        p = p.but(extra_margin=args.extra)
    if args.stability is not None:
        p = p.but(stability_ms=args.stability)
    return p


def add_param_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--octave", action="store_true", help="verify exact notes (decision E)")
    ap.add_argument(
        "--harmonic-exclusion",
        action="store_true",
        help="exempt harmonics of targets from the extra-note check (decision A)",
    )
    ap.add_argument("--fft", type=int, default=None)
    ap.add_argument("--present", type=float, default=None)
    ap.add_argument("--extra", type=float, default=None)
    ap.add_argument("--stability", type=float, default=None, help="stability window in ms")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="spike.score", description="Score the recorded corpus. The go/no-go call."
    )
    add_param_args(ap)
    ap.add_argument("--json", type=Path, default=None, help="also write results as JSON")
    ap.add_argument("--item", default=None, help="score only these item ids (comma separated)")
    ap.add_argument("--corpus", type=Path, default=CORPUS_DIR, help="corpus directory")
    args = ap.parse_args(argv)

    params = build_params(args)
    entries = load_corpus(args.corpus)

    skipped = [e for e in entries if not applicable(e, params)]
    entries = [e for e in entries if applicable(e, params)]
    if skipped:
        names = sorted({e["item"] for e in skipped})
        print(
            f"{DIM}skipping {len(skipped)} take(s) not meaningful in this mode: "
            f"{', '.join(names)}{RESET}"
        )

    if args.item:
        wanted = {s.strip() for s in args.item.split(",")}
        entries = [e for e in entries if e["item"] in wanted]
        if not entries:
            raise SystemExit("no takes matched --item")

    results = [score_take(e, params, args.corpus) for e in entries]
    print(report(results, params))

    if args.json:
        overall = summarise(results)
        args.json.write_text(
            json.dumps(
                {
                    "params": {
                        k: (list(v) if isinstance(v, tuple) else v)
                        for k, v in params.__dict__.items()
                    },
                    "recall": overall.recall,
                    "false_confirm_rate": overall.false_confirm_rate,
                    "median_latency_ms": overall.median_latency,
                    "takes": [asdict(r) for r in results],
                },
                indent=2,
            )
            + "\n"
        )
        print(f"{DIM}wrote {args.json}{RESET}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
