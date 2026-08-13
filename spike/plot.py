"""Visual diagnosis of a single take.

    uv run python -m spike.plot spike/corpus/c2_single__medium__take1.wav
    uv run python -m spike.plot --item c2_single

An aggregate recall number says a take failed. It does not say whether the
target was absent, drowned by an extra, never onset-gated, or present but too
briefly. Those have different fixes, and the four stacked panels here separate
them at a glance:

  1. spectrogram        — is the energy where the notes are?
  2. chroma over time   — target rows highlighted, extras marked
  3. flux vs threshold  — did the strike register as an onset at all?
  4. verdict timeline   — which of the four conditions was the blocker, per frame

Writes a PNG next to the corpus by default (`spike/plots/`, gitignored — these
are regenerated on demand rather than stored).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from . import audioio
from .core.chroma import PITCH_CLASS_NAMES, midi_to_hz
from .core.pipeline import analyse
from .params import DEFAULT, Params
from .record import CORPUS_DIR, MANIFEST
from .score import add_param_args, build_params, load_corpus, target_for

PLOT_DIR = Path(__file__).parent / "plots"


def plot_take(
    samples: np.ndarray,
    entry: dict | None,
    target: tuple[int, ...],
    params: Params,
    out: Path,
) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    results = analyse(samples, target, params)
    if not results:
        raise SystemExit("signal too short to produce a single analysis frame")

    times = np.array([r.time_ms / 1000.0 for r in results])
    chroma = np.array([r.chroma for r in results]).T  # (12, frames)
    flux = np.array([r.flux for r in results])
    thresh = np.array([r.flux_threshold for r in results])

    fig, axes = plt.subplots(
        4, 1, figsize=(13, 11), sharex=True,
        gridspec_kw={"height_ratios": [2.2, 2.2, 1.0, 1.2]},
    )

    title = entry["name"] if entry else out.stem
    mode = "octave" if params.octave_mode else "pitch class"
    fig.suptitle(
        f"{title}   ·   target {_target_text(target, params)}   ·   {mode} mode",
        fontsize=13,
    )

    # 1 — spectrogram, log frequency, limited to the analysed band.
    ax = axes[0]
    spec_db = 20 * np.log10(
        np.array([np.abs(np.fft.rfft(f * np.hanning(len(f))))
                  for f in _frames(samples, params)]).T + 1e-10
    )
    freqs = np.fft.rfftfreq(params.fft_size, 1.0 / params.sample_rate)
    lo, hi = float(midi_to_hz(params.midi_low)) * 0.7, float(midi_to_hz(params.midi_high)) * 1.4
    band = (freqs >= lo) & (freqs <= hi)
    ax.pcolormesh(times, freqs[band], spec_db[band], shading="nearest", cmap="magma")
    ax.set_yscale("log")
    ax.set_ylabel("Hz (log)")
    for m in (target if params.octave_mode else ()):
        ax.axhline(float(midi_to_hz(m)), color="cyan", lw=0.8, alpha=0.6)
    ax.set_title("spectrogram — is the energy where the notes should be?", fontsize=9, loc="left")

    # 2 — chroma heat map with the target pitch classes called out.
    ax = axes[1]
    ax.pcolormesh(times, np.arange(12), chroma, shading="nearest", cmap="viridis")
    ax.set_yticks(np.arange(12))
    target_pcs = {t % 12 for t in target}
    ax.set_yticklabels(
        [
            f"{'▶ ' if pc in target_pcs else '  '}{PITCH_CLASS_NAMES[pc]}"
            for pc in range(12)
        ],
        fontsize=8,
    )
    ax.set_ylabel("pitch class")
    ax.set_title(
        "chroma — target rows marked ▶; a bright unmarked row is an extra",
        fontsize=9, loc="left",
    )

    # 3 — onset detection function against its adaptive threshold.
    ax = axes[2]
    ax.plot(times, flux, lw=1.0, label="spectral flux")
    ax.plot(times, thresh, lw=1.0, ls="--", color="orange", label="adaptive threshold")
    for r in results:
        if r.onset:
            ax.axvline(r.time_ms / 1000.0, color="cyan", lw=1.2, alpha=0.8)
    ax.set_ylabel("flux")
    ax.legend(fontsize=7, loc="upper right")
    ax.set_title("onset — cyan lines are accepted strikes (decision F)", fontsize=9, loc="left")

    # 4 — which condition was blocking, frame by frame.
    ax = axes[3]
    rows = [
        ("armed (onset seen)", [bool(r.verdict and r.verdict.armed) for r in results], "#4c9f70"),
        ("targets present", [bool(r.verdict and r.verdict.targets_present) for r in results], "#3d7ea6"),
        ("no extras", [bool(r.verdict and not r.verdict.extras and not r.verdict.silent) for r in results], "#b5772e"),
        ("CONFIRMED", [bool(r.verdict and r.verdict.latched) for r in results], "#c2452d"),
    ]
    for i, (label, mask, colour) in enumerate(rows):
        ax.fill_between(times, i, i + 0.8, where=np.array(mask), color=colour, step="mid")
        ax.text(times[0], i + 0.4, "  " + label, va="center", fontsize=8, color="black")
    ax.set_ylim(0, len(rows))
    ax.set_yticks([])
    ax.set_xlabel("seconds")
    ax.set_title(
        "verdict — confirmation needs all three upper bars simultaneously, "
        f"held {params.stability_frames} frames",
        fontsize=9, loc="left",
    )

    fig.tight_layout(rect=(0, 0, 1, 0.97))
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out


def _frames(samples: np.ndarray, params: Params) -> list[np.ndarray]:
    from .core import dsp

    return list(dsp.iter_frames(samples, params.fft_size, params.hop))


def _target_text(target: tuple[int, ...], params: Params) -> str:
    from .core.chroma import note_name

    if params.octave_mode:
        return " ".join(note_name(m) for m in target)
    return " ".join(PITCH_CLASS_NAMES[pc] for pc in target)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="spike.plot", description="Plot spectrogram, chroma, onsets and verdict for a take."
    )
    ap.add_argument("wav", nargs="?", type=Path, help="path to a WAV file")
    ap.add_argument("--item", default=None, help="plot every take of a corpus item id")
    ap.add_argument("--target", default=None, help="override the target, e.g. C,E,G")
    ap.add_argument("--out", type=Path, default=None, help="output PNG path")
    ap.add_argument("--corpus", type=Path, default=CORPUS_DIR, help="corpus directory")
    add_param_args(ap)
    args = ap.parse_args(argv)

    params = build_params(args)

    if args.item:
        entries = [e for e in load_corpus(args.corpus) if e["item"] == args.item]
        if not entries:
            raise SystemExit(f"no corpus takes for item {args.item!r}")
        for entry in entries:
            samples, _ = audioio.read_wav(args.corpus / entry["file"])
            out = PLOT_DIR / f"{entry['stem']}.png"
            print(plot_take(samples, entry, target_for(entry, params), params, out))
        return 0

    if not args.wav:
        raise SystemExit("give a WAV path or --item <corpus item id>")

    samples, rate = audioio.read_wav(args.wav)
    if rate != params.sample_rate:
        params = params.but(sample_rate=rate)

    entry = None
    if (args.corpus / MANIFEST.name).exists():
        entry = next(
            (e for e in load_corpus(args.corpus) if e["file"] == args.wav.name), None
        )

    if args.target:
        from .notation import parse_target

        target = parse_target(args.target, params.octave_mode)
    elif entry:
        target = target_for(entry, params)
    else:
        raise SystemExit("this WAV is not in the manifest — pass --target explicitly")

    out = args.out or PLOT_DIR / f"{args.wav.stem}.png"
    print(plot_take(samples, entry, target, params, out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
