# piano-coach

A wait-mode piano learning **web page**. It listens to a real piano through the
microphone, shows falling notes, and waits for the correct notes before
advancing. No install, no MIDI hardware, no backend.

Two features: recognise the keys and chords played through a microphone, and
play any song with a wait-mode learning loop.

- [`piano-coach-plan.md`](piano-coach-plan.md) — the plan
- [`piano-coach-decisions.md`](piano-coach-decisions.md) — locked decisions and why
- [`spike/`](spike/) — **the validation spike: where the project currently is**

---

## Status

**Milestone 1 of the build order: the validation spike.** Not the app.

Both planning documents make this a hard gate (DECISION #12, and PLAN's "prove
it before building the UI"). The spike is a measuring instrument: record a
labelled corpus of chords on a real piano, run chroma verification over it
offline, and get a number that says build / don't build / build with
constraints.

The tooling is built and tested (29 tests, no microphone required). What is
missing is a corpus recorded on a real piano — see
[`spike/README.md`](spike/README.md).

Nothing downstream — MIDI parsing, the piano-roll, the wait-mode loop, any
browser code — exists yet, by design.

```bash
uv run pytest                            # the maths, on synthetic signals
uv run python -m spike.live --check      # the microphone
uv run python -m spike.live              # the pipeline, live
uv run python -m spike.record            # record the corpus
uv run python -m spike.score             # the go/no-go measurement
```

---

## The idea the project rests on

**In wait-mode you verify, not transcribe.**

"Listen to a microphone and identify any chord" is a research problem. But in
wait-mode the app already knows the next notes from the song file. The question
is not *what did they play?* but *are these specific known notes present in the
sound right now?*

That changes everything downstream:

- **Latency stops mattering.** A held chord rings about a second; 300-700 ms to
  confirm is fine.
- **Detection becomes targeted.** Check for energy at the expected fundamentals
  and harmonics of known notes. Plain FFT, real time, no machine learning.

This is what turns microphone polyphony from a months-long wall into something
buildable.

---

## Hard limits — stated up front

- **No finger detection from a microphone.** Finger numbers are displayed as
  guidance and are never verified. Nothing in the audio reveals which finger
  pressed a key.
- **"Any song" means any MIDI file you can obtain.** Not arbitrary audio.
  Turning an MP3 into notes is a different and much harder problem.
- **Feasibility is hardware-dependent.** The spike measures one piano, one
  room, one microphone. Its result does not automatically transfer to a
  different setup.
- **Harmonic and octave ambiguity is the core technical risk.** A low C
  radiates energy at higher C, G and E, so those notes look played when they
  are not. Verification survives this better than transcription does, but
  per-note harmonic weighting and extra-note rejection are mandatory, not
  refinements.
- **The on-screen keyboard is a guide, never an input.** Input is always the
  microphone.

---

## Architecture

```
   mic ─▶ Web Audio (FFT) ─▶ chroma + harmonic energy
                                │
     ┌──────────────────────────┴──────────────────────────┐
LAYER 1: VERIFICATION                 LAYER 2: FREE RECOGNITION
(wait-mode — the product)             ("what am I playing?" — later, optional)
• target notes known                  • target unknown
• FFT → are target pitches present?   • basic-pitch or Onsets & Frames
• real-time, no ML                    • ~0.5-1 s lag
```

Layer 1 is roughly 90% of the app and needs no ML. It is what the spike
measures. Layer 2 is a bolt-on that is not on the v1 path.

---

## Two languages, on purpose

The **spike is Python** (numpy): offline DSP tuning, threshold sweeps and
spectrogram plots are what numpy is for, and iterating on thresholds in a
browser would be miserable.

The **app stays a browser page** (DECISIONS #10 and #11 are unchanged): Web
Audio, Canvas, static hosting over HTTPS, no install.

The cost is that the DSP gets written twice. That cost is contained
deliberately:

- `spike/core/` uses **plain numpy only** — no scipy, no librosa — so every
  operation has a direct browser equivalent.
- [`spike/ALGORITHM.md`](spike/ALGORITHM.md) specifies the exact maths,
  including the traps (periodic vs symmetric Hann, cents-based band widths,
  bin rounding).
- `spike/golden/golden.json` holds acceptance vectors the port must reproduce,
  split into transform tests and state-machine tests so a failure says *which
  half* is wrong.

Without that, a spike that passes in Python and a port that behaves differently
would mean the gate measured nothing.

---

## Development

Requires [uv](https://docs.astral.sh/uv/). Python 3.14, three dependencies
(numpy, sounddevice, matplotlib).

```bash
uv sync
uv run pytest
```

On macOS the terminal or IDE running Python needs Microphone permission
(System Settings → Privacy & Security → Microphone). Without it, Core Audio
returns silence rather than an error — `spike.live --check` detects this.
