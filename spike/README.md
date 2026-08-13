# The validation spike

**This is the go/no-go gate for the whole project.** Both planning documents
insist on it before any UI exists:

> "The validation spike is the real go/no-go. Everything downstream assumes
> chroma-verification clears ~90% on the target hardware. Prove it before
> building the UI." — PLAN, Risks

The question it answers is narrow and answerable: *on your piano, through your
microphone, can we tell whether a known set of notes is currently sounding?*

Not "can we transcribe piano audio" — that is a research problem. Verification
against a known target is a different and much easier question (DECISION #2),
and this measures how much easier.

---

## Run it in this order

The first three steps need no recordings and take about two minutes.

```bash
# 1. Does the maths work at all? Synthetic signals, no microphone.
uv run pytest

# 2. Does the microphone work? Prints device, level, clipping.
uv run python -m spike.live --check

# 3. Does the pipeline work? Play a C, watch the C bin light up.
uv run python -m spike.live
uv run python -m spike.live --target C,E,G      # with live verdicts

# 4. Record the corpus. ~41 takes, roughly 10 minutes. This step is yours.
uv run python -m spike.record

# 5. Score it. This is the measurement.
uv run python -m spike.score

# 6. Tune. Settles open decisions A, B and E with data.
uv run python -m spike.sweep

# 7. Diagnose whatever failed.
uv run python -m spike.plot --item c2_single

# 8. Freeze the result for the JS port.
uv run python -m spike.export_golden
```

Then write up `RESULTS.md`.

---

## Before you record

These matter more than any threshold in the code:

- **Reverb, ambience and effects OFF on the piano.** Reverb smears the attack
  transients that onset gating depends on. A clean acoustic-grand voice.
- **No clipping.** `--check` must report 0.00%. A clipped take generates
  harmonic distortion that looks exactly like extra notes, so it corrupts the
  extra-note measurement while appearing to be a real result.
- **Microphone about a metre away**, fans and HVAC off.
- **macOS microphone permission.** System Settings → Privacy & Security →
  Microphone, enable whichever app runs Python (Terminal, iTerm, VS Code), then
  restart it completely. Without permission macOS returns a stream of *zeros*
  rather than raising an error — every number stays well-formed and meaningless.
  That is what `--check` exists to catch.

The recorder prompts one take at a time, reviews each for clipping and level,
and offers a retake. Interrupted runs resume where they stopped.

---

## What the corpus deliberately contains

A corpus of ten clean mid-register triads would score beautifully and prove
nothing, because nothing here is expected to fail on clean mid-register triads.
Each item names the question it answers — see `corpus_script.py`.

| Item | Question |
|---|---|
| C2 / C4 / C6 alone | per-register baseline; C2 is the bass-resolution test |
| C major, F major, A minor | the ordinary case, and that it is not C-specific |
| G7 | four notes spread chroma thinner and collide more overtones |
| C4 + D4 | can adjacent keys be told apart? |
| C4 + C5 | decision E in one item |
| C4 alone vs a C4+C5 target | *negative* — C4's 2nd harmonic **is** C5 (octave mode only) |
| pedal down, C ringing, asked for F | *negative* — the runaway-advance failure |
| pedal down, C then F | the hardest positive: C rings under F |
| F major played, C major asked | *negative* — a near miss sharing one pitch class |
| room tone | *negative* — the noise floor must never confirm |

Three dynamics per positive item, to prove loudness independence.

---

## Reading the verdict

`score.py` checks three bars, all taken from the project's own documents:

| Metric | Bar | Source |
|---|---|---|
| recall on positives | ≥ 90% | PLAN's own number |
| false confirms on negatives | ≤ 5% | PLAN's extra-note warning |
| median onset-to-confirm | ≤ 700 ms | DECISIONS #3 |

Three outcomes, all useful:

- **GO** — proceed to PLAN step 3 with the tuned constants.
- **CONSTRAINED GO** — e.g. mid and treble clear but C2 does not. Ship with a
  stated range and filter songs to it. The per-register breakdown exists to make
  this call available instead of collapsing a partial success into a flat
  failure.
- **NO-GO** — reconsider the mic path. Learning the ceiling in an afternoon
  rather than after building a UI *is* the spike succeeding.

False confirms are ranked above recall throughout, including in the sweep's
ordering. A missed note means the player presses a key again. A false confirm
means the song advanced past a note that was never played — which cascades, and
which wait-mode gives the player no way to recover from.

---

## Files

```
core/            pure numpy: no I/O, no globals, no clock — this is what ports
  dsp.py           framing, periodic Hann, rfft → magnitudes
  chroma.py        spectrum → per-note energies → 12-bin chroma
  onset.py         spectral flux, adaptive threshold, refractory gate
  verify.py        the four-condition state machine
  pipeline.py      wiring, plus precompute/replay used by the sweep
params.py        every tunable constant, with the reasoning for each
notation.py      "C,E,G" and "C4,E4,G4" parsing
audioio.py       WAV via stdlib `wave`, microphone via sounddevice
corpus_script.py what to play and why each item is in the corpus
record.py        guided capture
live.py          real-time meter and --check
score.py         the measurement
sweep.py         grid search → operating point
plot.py          four-panel visual diagnosis
synth_corpus.py  fake corpus for exercising the tools without a piano
export_golden.py acceptance vectors for the JS port
ALGORITHM.md     the exact maths — the port contract
RESULTS.md       written after the corpus run: the verdict
```

`core/` is quarantined from I/O on purpose. That boundary is what makes it
unit-testable, cheap to sweep, and portable to the browser without dragging an
audio backend along.

---

## Testing the tools without a piano

```bash
uv run python -m spike.synth_corpus /tmp/fake-corpus
uv run python -m spike.score  --corpus /tmp/fake-corpus
uv run python -m spike.sweep  --corpus /tmp/fake-corpus --quick
```

Useful for checking the tooling runs, and as a fixed reference while tuning: if
a change breaks the synthetic corpus, it is broken in the algorithm rather than
in the room.

**It proves nothing about hardware.** Synthetic partials are cleaner than any
piano through any microphone. A GO on synthetic audio is not a GO.
