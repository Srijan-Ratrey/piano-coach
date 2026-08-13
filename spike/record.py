"""Guided corpus recorder — PLAN build-order step 1, the half only you can do.

    uv run python -m spike.record

Walks the script in `corpus_script.py` one take at a time, writes
`spike/corpus/<item>__<dynamic>__take<n>.wav` and appends to
`spike/corpus/manifest.json`.

Two things it does beyond writing files, both of which exist because a corpus
with a hidden defect produces a confident and wrong go/no-go call:

- It checks every take for clipping and near-silence and offers a retake on the
  spot. Discovering at scoring time that a third of the corpus was clipped
  means re-recording all of it.
- It stamps the input device, sample rate and timestamp into the manifest, so a
  result can always be traced back to the hardware it was measured on
  (DECISIONS: "feasibility is hardware-dependent").

Interrupted runs resume: takes already in the manifest are skipped.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

from . import audioio
from .core import dsp
from .corpus_script import SCRIPT, PlannedTake, plan
from .params import CLIP_THRESHOLD, DEFAULT, Params

BOLD = "\x1b[1m"
DIM = "\x1b[2m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
RED = "\x1b[31m"
CYAN = "\x1b[36m"
RESET = "\x1b[0m"

CORPUS_DIR = Path(__file__).parent / "corpus"
MANIFEST = CORPUS_DIR / "manifest.json"

TAKE_SECONDS = 4.0
"""Long enough for a beat of lead-in silence, an attack, and ~2.5 s of sustain.
The lead-in is not padding: onset detection needs pre-attack frames to
establish what 'quiet' sounds like before the strike."""


def load_manifest() -> list[dict]:
    if not MANIFEST.exists():
        return []
    return json.loads(MANIFEST.read_text())


def save_manifest(entries: list[dict]) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(entries, indent=2) + "\n")


def describe(p: PlannedTake, total: int) -> str:
    item = p.item
    dyn = {"soft": "SOFT (pp)", "medium": "MEDIUM (mf)", "loud": "LOUD (ff)"}[p.dynamic]
    lines = [
        "",
        f"{DIM}{'─' * 68}{RESET}",
        f"{BOLD}[{p.index}/{total}]  {item.name}{RESET}   {CYAN}{dyn}{RESET}"
        + (f"   take {p.take}" if item.takes > 1 else ""),
        "",
        f"  play      {BOLD}{item.played_names or 'nothing'}{RESET}",
    ]
    if item.target != item.played:
        lines.append(
            f"  scored vs {YELLOW}{item.target_names}{RESET}   "
            f"{DIM}(deliberate mismatch — negative control){RESET}"
        )
    if item.pedal:
        lines.append(f"  pedal     {YELLOW}DOWN{RESET}")
    lines += [
        "",
        f"  {item.instruction}",
        f"  {DIM}{item.why}{RESET}",
        "",
    ]
    return "\n".join(lines)


def review(samples: np.ndarray, params: Params, item_kind: str) -> tuple[bool, str]:
    """Return `(looks_ok, message)` for a freshly recorded take."""
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    level = float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0
    clip = dsp.clipped_fraction(samples, CLIP_THRESHOLD)

    stats = f"peak {peak:.3f}  rms {level:.4f}  clipped {clip * 100:.2f}%"

    if clip > 0.0:
        return False, f"{RED}CLIPPED{RESET}  {stats}\n  Distortion looks like extra notes. Turn the volume down and retake."
    if item_kind == "negative" and not samples.size:
        return True, stats
    if item_kind != "negative" and peak < 0.02:
        return False, f"{YELLOW}TOO QUIET{RESET}  {stats}\n  Below 2% of full scale; soft frames will fall under the silence gate."
    if item_kind == "negative" and peak > 0.5:
        return False, f"{YELLOW}LOUD FOR A CONTROL{RESET}  {stats}\n  Room tone should be quiet — was something played by accident?"
    return True, f"{GREEN}ok{RESET}  {stats}"


def countdown() -> None:
    import time

    for n in ("3", "2", "1"):
        sys.stdout.write(f"  {DIM}{n}…{RESET}\r")
        sys.stdout.flush()
        time.sleep(0.6)
    sys.stdout.write(f"  {GREEN}{BOLD}GO — play now{RESET}          \n")
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="spike.record",
        description="Record the labelled chord corpus for the validation spike.",
    )
    ap.add_argument("--device", type=int, default=None, help="input device index")
    ap.add_argument(
        "--extra-takes",
        type=int,
        default=0,
        help="additional takes per item beyond the script's default",
    )
    ap.add_argument(
        "--only",
        default=None,
        help="comma-separated item ids to record (default: the whole script)",
    )
    ap.add_argument(
        "--redo",
        action="store_true",
        help="re-record takes already present in the manifest",
    )
    ap.add_argument(
        "--seconds", type=float, default=TAKE_SECONDS, help="length of each take"
    )
    args = ap.parse_args(argv)

    params = DEFAULT
    script = SCRIPT
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        script = tuple(i for i in SCRIPT if i.id in wanted)
        unknown = wanted - {i.id for i in SCRIPT}
        if unknown:
            print(f"{RED}unknown item ids: {', '.join(sorted(unknown))}{RESET}")
            return 2
        if not script:
            print(f"{RED}nothing selected{RESET}")
            return 2

    takes = plan(script, args.extra_takes)
    entries = load_manifest()
    done = {e["stem"] for e in entries}

    device_name = audioio.device_name(args.device)

    print(f"{BOLD}piano-coach — corpus recorder{RESET}")
    print(f"  device      {device_name}")
    print(f"  sample rate {params.sample_rate} Hz, 16-bit mono")
    print(f"  output      {CORPUS_DIR}")
    print(f"  takes       {len(takes)} planned, {len(done)} already recorded")
    print()
    print(f"{DIM}Before starting: reverb/ambience OFF on the piano, a clean acoustic{RESET}")
    print(f"{DIM}grand voice, mic about a metre away, and run `--check` on spike.live{RESET}")
    print(f"{DIM}first to confirm the level is sane. Reverb smears the onsets that the{RESET}")
    print(f"{DIM}pedal items depend on.{RESET}")
    print()
    print(f"{DIM}At each prompt: Enter = record, r = retake, s = skip, q = quit.{RESET}")

    for p in takes:
        if p.stem in done and not args.redo:
            continue

        print(describe(p, len(takes)))
        while True:
            try:
                choice = input(f"  {BOLD}Enter{RESET} to record  {DIM}(s skip, q quit){RESET} ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n  stopped.")
                save_manifest(entries)
                return 0

            if choice == "q":
                save_manifest(entries)
                print(f"\n  saved {len(entries)} takes to {MANIFEST}")
                return 0
            if choice == "s":
                break

            countdown()
            samples = audioio.record(args.seconds, params.sample_rate, args.device)
            print(f"  {DIM}STOP{RESET}")

            ok, message = review(samples, params, p.item.kind)
            print(f"  {message}")

            if not ok:
                again = input(f"  {YELLOW}retake?{RESET} [Y/n] ").strip().lower()
                if again in ("", "y", "yes"):
                    continue

            path = CORPUS_DIR / f"{p.stem}.wav"
            audioio.write_wav(path, samples, params.sample_rate)

            entries = [e for e in entries if e["stem"] != p.stem]
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
                    "device": device_name,
                    "peak": float(np.max(np.abs(samples))) if samples.size else 0.0,
                    "rms": float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0,
                    "clipped": dsp.clipped_fraction(samples, CLIP_THRESHOLD),
                    "recorded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                }
            )
            save_manifest(entries)
            print(f"  {DIM}saved {path.name}{RESET}")
            break

    save_manifest(entries)
    print()
    print(f"{GREEN}{BOLD}Corpus complete.{RESET} {len(entries)} takes in {CORPUS_DIR}")
    print(f"Next: {BOLD}uv run python -m spike.score{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
