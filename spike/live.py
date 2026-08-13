"""Real-time chroma meter — PLAN build-order step 2.

    "Live chroma meter — mic -> real-time 12-bin display. Play a note, watch
    the right bin light. Proves the audio pipeline."

Worth running before recording anything. It answers, in about a minute,
questions that would otherwise be confounded with detection quality later:
is the mic actually delivering audio, is the level sane, is the room quiet
enough, does the piano's own reverb setting need turning off.

    uv run python -m spike.live --check
    uv run python -m spike.live
    uv run python -m spike.live --target C,E,G

Runs the exact same `core/` pipeline the offline scorer runs, so what is seen
here is what gets measured later.
"""

from __future__ import annotations

import argparse
import queue
import sys

import numpy as np

from . import audioio
from .core import dsp
from .core.chroma import PITCH_CLASS_NAMES
from .core.pipeline import Analyser
from .notation import format_target, parse_target
from .params import CLIP_THRESHOLD, DEFAULT, Params

BAR_WIDTH = 44
CLEAR = "\x1b[2J"
HOME = "\x1b[H"
DIM = "\x1b[2m"
BOLD = "\x1b[1m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
RED = "\x1b[31m"
CYAN = "\x1b[36m"
RESET = "\x1b[0m"


def check(params: Params, device: int | None, seconds: float = 2.0) -> int:
    """Diagnose the input path. Returns a process exit code.

    Exists because of a specific macOS failure mode: when microphone
    permission has not been granted, Core Audio hands back a stream of zeros
    instead of raising. Every downstream number stays perfectly well-formed and
    completely meaningless, so the silence has to be checked for explicitly.
    """
    print(f"{BOLD}Input devices{RESET}")
    for index, name, channels in audioio.list_input_devices():
        marker = (
            "*"
            if (device is None and index == _default_index()) or index == device
            else " "
        )
        print(f"  {marker} [{index}] {name}  ({channels} in)")

    print(f"\n{BOLD}Recording {seconds:g}s from:{RESET} {audioio.device_name(device)}")
    print(f"{DIM}Play a few notes now.{RESET}")

    samples = audioio.record(seconds, params.sample_rate, device)

    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    level = dsp.rms(samples)
    clip = dsp.clipped_fraction(samples, CLIP_THRESHOLD)

    print(f"\n  sample rate   {params.sample_rate} Hz")
    print(f"  samples       {samples.size}")
    print(f"  peak          {peak:.4f}")
    print(f"  rms           {level:.4f}")
    print(f"  clipped       {clip * 100:.2f}%")

    if peak == 0.0:
        print(f"\n{RED}{audioio.MIC_PERMISSION_HINT}{RESET}")
        return 1

    print()
    if clip > 0.0:
        print(
            f"{RED}CLIPPING.{RESET} Turn the piano's speakers down or move the mic back."
        )
        print("A clipped take generates harmonic distortion that looks exactly like")
        print("extra notes, so it would corrupt the extra-note measurement.")
        return 1
    if peak < 0.02:
        print(f"{YELLOW}Very quiet.{RESET} Peak is under 2% of full scale — raise the")
        print("piano's volume or move the mic closer, or soft takes will fall below")
        print(f"the silence gate (rms < {params.silence_rms}).")
        return 1

    print(f"{GREEN}Input looks usable.{RESET} Peak {peak:.2f}, no clipping.")
    return 0


def _default_index() -> int:
    import sounddevice as sd

    idx = sd.default.device[0]
    return -1 if idx is None else int(idx)


def _bar(value: float, peak: float, width: int = BAR_WIDTH) -> str:
    if peak <= 0:
        return " " * width
    filled = int(round(width * min(1.0, value / peak)))
    return "█" * filled + " " * (width - filled)


def _render(result, params: Params, target, octave_mode: bool, device_name: str) -> str:
    chroma = result.chroma
    peak = float(chroma.max()) if chroma.size else 0.0
    verdict = result.verdict

    target_set = set(target or ())
    # In octave mode the 12-bin display still shows pitch classes — the bars are
    # a human aid, while the verdict below them reflects whichever mode is live.
    display_targets = {t % 12 for t in target_set}
    extras = {e % 12 for e in (verdict.extras if verdict else ())}

    lines = [
        f"{BOLD}piano-coach{RESET} {DIM}live chroma meter{RESET}"
        f"{DIM}   device: {device_name}{RESET}",
        "",
    ]

    onset_tag = f"{CYAN}● ONSET{RESET}" if result.onset else f"{DIM}·      {RESET}"
    silent = result.rms < params.silence_rms
    level_tag = f"{DIM}silent{RESET}" if silent else f"rms {result.rms:.4f}"
    lines.append(f"  {level_tag}   {onset_tag}   {DIM}frame {result.index}{RESET}")
    lines.append("")

    present_level = params.present_thresh * peak
    extra_level = params.extra_margin * peak

    for pc in range(12):
        value = float(chroma[pc])
        name = PITCH_CLASS_NAMES[pc].ljust(2)
        bar = _bar(value, peak)

        if pc in display_targets:
            colour = GREEN if value >= present_level else YELLOW
            tag = "target"
        elif pc in extras:
            colour = RED
            tag = "EXTRA"
        elif value > extra_level:
            colour = RED
            tag = "loud"
        else:
            colour = DIM
            tag = ""

        lines.append(f"  {colour}{name} {bar}{RESET} {value:5.3f} {DIM}{tag}{RESET}")

    lines.append("")
    if verdict is None:
        lines.append(f"  {DIM}no target — free-running meter{RESET}")
    else:
        held = f"{verdict.frames_held}/{params.stability_frames}"
        if verdict.latched:
            state = f"{GREEN}{BOLD}CONFIRMED{RESET}"
        elif verdict.silent:
            state = f"{DIM}silent{RESET}"
        elif not verdict.armed:
            state = f"{YELLOW}waiting for onset{RESET}"
        elif verdict.extras:
            names = " ".join(sorted(PITCH_CLASS_NAMES[e % 12] for e in verdict.extras))
            state = f"{RED}extra: {names}{RESET}"
        elif not verdict.targets_present:
            state = f"{YELLOW}target not present{RESET}"
        else:
            state = f"{CYAN}holding{RESET}"
        lines.append(
            f"  target {BOLD}{format_target(target, octave_mode)}{RESET}"
            f"   {state}   {DIM}held {held}{RESET}"
        )

    lines.append("")
    lines.append(f"  {DIM}Ctrl-C to stop{RESET}")
    return "\n".join(line + "\x1b[K" for line in lines)


def meter(params: Params, device: int | None, target, octave_mode: bool) -> int:
    import sounddevice as sd

    analyser = Analyser(params, target)
    device_name = audioio.device_name(device)
    blocks: queue.Queue[np.ndarray] = queue.Queue()

    def callback(indata, _frames, _time, status):
        if status:
            print(status, file=sys.stderr)
        # Copy: sounddevice reuses the buffer, and the analysis runs on the
        # main thread, not here.
        blocks.put(indata[:, 0].copy().astype(np.float64))

    window = np.zeros(params.fft_size, dtype=np.float64)
    seen_audio = False
    frames_seen = 0

    sys.stdout.write(CLEAR)
    try:
        with sd.InputStream(
            samplerate=params.sample_rate,
            blocksize=params.hop,
            channels=1,
            dtype="float32",
            device=device,
            callback=callback,
        ):
            while True:
                block = blocks.get()
                n = len(block)
                window = np.concatenate([window[n:], block])
                result = analyser.push(window)
                frames_seen += 1
                if result.rms > 0:
                    seen_audio = True

                sys.stdout.write(HOME)
                sys.stdout.write(
                    _render(result, params, target, octave_mode, device_name)
                )
                sys.stdout.flush()

                if frames_seen == 40 and not seen_audio:
                    sys.stdout.write(
                        "\n\n" + RED + audioio.MIC_PERMISSION_HINT + RESET + "\n"
                    )
                    sys.stdout.flush()
                    return 1
    except KeyboardInterrupt:
        sys.stdout.write("\n")
        return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="spike.live",
        description="Real-time 12-bin chroma meter over the microphone.",
    )
    ap.add_argument(
        "--check",
        action="store_true",
        help="record a short clip and report device, level and clipping, then exit",
    )
    ap.add_argument("--device", type=int, default=None, help="input device index")
    ap.add_argument(
        "--target",
        default=None,
        help="show live verdicts against a target, e.g. C,E,G (or C4,E4,G4 with --octave)",
    )
    ap.add_argument(
        "--octave",
        action="store_true",
        help="verify exact MIDI notes rather than pitch classes (decision E)",
    )
    ap.add_argument("--fft", type=int, default=None, help="override FFT size")
    args = ap.parse_args(argv)

    params = DEFAULT.but(octave_mode=args.octave)
    if args.fft:
        params = params.but(fft_size=args.fft)

    if args.check:
        return check(params, args.device)

    target = parse_target(args.target, args.octave) if args.target else None
    return meter(params, args.device, target, args.octave)


if __name__ == "__main__":
    raise SystemExit(main())
