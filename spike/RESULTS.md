# Spike results — the go/no-go call

> **Status: NOT YET RUN.** No corpus has been recorded. Everything below is the
> template to fill in after `spike/record.py` and `spike/score.py`.
>
> The tooling is built and tested; what is missing is a recording of a real
> piano, which is the only thing that can answer the question. Until this file
> has numbers in it, nothing downstream of PLAN step 2 should be built —
> DECISION #12.

---

## Setup

Fill from `spike/corpus/manifest.json`, which stamps the device into every take.

| | |
|---|---|
| Piano | *(model; digital via speakers per DECISION #17)* |
| Voice / effects | *(should be a clean acoustic grand, reverb OFF)* |
| Microphone | *(from the manifest `device` field)* |
| Distance | |
| Room | *(noise sources, approximate size)* |
| Date | |
| Takes recorded | |
| Any clipping? | *(`score.py` reports it per take — should be 0.00%)* |

---

## Headline numbers

At the default operating point (`spike/params.py` as committed):

| Metric | Bar | Measured |
|---|---|---|
| Recall on positives | ≥ 90% | |
| False confirms on negatives | ≤ 5% | |
| Median onset-to-confirm | ≤ 700 ms | |

By register — this is the table that decides between NO-GO and CONSTRAINED GO:

| Register | Recall | Notes |
|---|---|---|
| bass (C2) | | *the register that set FFT_SIZE=16384; 2.93 Hz bins vs a 3.9 Hz semitone gap* |
| mid | | |
| treble (C6) | | |

---

## Tuned operating point

From `spike/sweep.py`. Paste the recommended block:

```python
FFT_SIZE =
PRESENT_THRESH =
EXTRA_MARGIN =
STABILITY_MS =
OCTAVE_MODE =
HARMONIC_EXCLUSION =
```

| Metric | At tuned point |
|---|---|
| Recall | |
| False confirms | |
| Median latency | |

**Cost of the tuning.** If the chosen point uses FFT 16384, note the latency
consequence: the window doubles to 341 ms and the onset refractory grows with
it, so time-to-confirm rises even though the thresholds did not change.

---

## Open decisions, now closed

Record the *measurement*, not just the choice — a future reader needs to know
whether a decision was clear-cut or marginal.

| # | Decision | Chosen | Evidence |
|---|---|---|---|
| A | Extra-note strictness (`EXTRA_MARGIN`, `HARMONIC_EXCLUSION`) | | |
| B | Stability window (`STABILITY_MS`) | | |
| E | Octave vs pitch class (`OCTAVE_MODE`) | | |
| F | Onset gating | *already forced — see `pedal_ring_only`* | |

For E specifically, the two items that settle it are `octave_c4_c5` (must
confirm) and `octave_c4_only` (must reject). If octave mode cannot reject a
C4+C5 target when only C4 is played, it is decorative and pitch-class mode wins.

---

## What failed, and why

For each failing item: what the plot showed, and which of the four conditions
was the blocker. `plot.py`'s bottom panel gives this directly — "targets
present" never lighting up means something quite different from "no extras"
flickering.

| Item | Blocker | Diagnosis |
|---|---|---|
| | | |

Distinguish *never close* (`hold` column stayed at 0) from *nearly confirmed*
(`hold` reached 4 or 5 of 6). They point at different fixes.

---

## Verdict

**GO / CONSTRAINED GO / NO-GO**

*One paragraph. If CONSTRAINED, state the constraint precisely enough to
implement — e.g. "songs restricted to C3-C6; the library filter must enforce
it and the README must say so."*

---

## What this does and does not establish

- Measured on **one** piano, in **one** room, through **one** microphone. The
  manifest records which. DECISIONS is explicit that feasibility is
  hardware-dependent; a second setup may land elsewhere.
- Digital piano through speakers has cleaner harmonics than an acoustic
  instrument, so these numbers are, if anything, **optimistic** for the
  acoustic case.
- Nothing here validates the browser implementation. The tuned constants above
  only transfer alongside `golden/golden.json`, and the port is not proven
  until it reproduces those vectors — DECISION #16.

---

## Next step

- **GO / CONSTRAINED GO** → PLAN build-order step 3 (single-note verification
  loop), then step 5 (MIDI + piano-roll). Port `core/` to JS against the golden
  vectors first, so the front end is built on a verified foundation.
- **NO-GO** → revisit DECISION #1. The realistic alternatives are MIDI input
  from a digital piano (which the target hardware may already support over USB,
  and which sidesteps this entire problem), or Layer 2 ML transcription with its
  ~1 s lag. Both are large changes to the locked decisions and neither should be
  taken without re-reading DECISIONS.md.
