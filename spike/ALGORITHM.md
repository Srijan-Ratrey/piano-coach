# Layer 1 verification — the algorithm

The exact maths behind `spike/core/`. This file plus `spike/golden/golden.json`
form the contract that the browser port must satisfy (DECISION #16): this
document says what to compute, the golden vectors say what the answers are.

Read it alongside the code — every section names the file it describes.

All constants live in `spike/params.py`. Values quoted here are the defaults;
`spike/RESULTS.md` records what the corpus actually chose.

---

## 0. What is being computed, and why it is not transcription

The app never asks "what is being played?". In wait-mode it already knows the
next notes from the song file, so the only question is "are *these specific*
pitches sounding right now?" (DECISION #2). Everything below is shaped by that:
there is no note tracker, no polyphonic estimator, no model. There is a target,
a spectrum, and four conditions.

---

## 1. Framing — `core/dsp.py`

Input is mono float audio at `SAMPLE_RATE` (48 000 Hz), nominally −1…1.

```
frame i  = samples[i*HOP : i*HOP + FFT_SIZE]        FFT_SIZE = 8192, HOP = 2048
windowed = frame * hann(FFT_SIZE)
spectrum = |rfft(windowed)|                          → FFT_SIZE/2 + 1 magnitudes
```

Partial frames at the end of a signal are **dropped, not zero-padded** — a
padded frame has a different effective window, so its magnitudes are not
comparable with its neighbours.

**The Hann window is periodic, not symmetric:**

```
w[k] = 0.5 − 0.5·cos(2πk / N)          k = 0 … N−1        (divisor N)
```

Not `N − 1`. `numpy.hanning` and MATLAB's `hann(N)` give the symmetric form;
`scipy.signal.get_window('hann', N)` and most JS FFT examples give the periodic
one. Getting this wrong shifts every magnitude slightly and quietly invalidates
every tuned threshold. `golden.json → checks.hann_periodic_8` pins it.

Magnitudes, not power. Chroma sums them linearly, and squaring would
over-weight the loudest partial, changing what the harmonic weights mean.

Derived: bin width `SAMPLE_RATE/FFT_SIZE` = **5.859 Hz**; window
**170.7 ms**; frame interval **42.67 ms**.

---

## 2. Note energies and chroma — `core/chroma.py`

Pitch is equal-tempered with A4 = 440 Hz:

```
f(m) = 440 · 2^((m − 69)/12)
```

For every MIDI note `m` in `[MIDI_LOW, MIDI_HIGH]` = [36, 96] (C2…C7) and each
harmonic `h` = 1…4 with weights `(1.0, 0.5, 0.25, 0.125)`:

```
f     = f(m) · h
lo    = round( f · 2^(−CENTS/1200) / bin_hz )        CENTS = 25
hi    = round( f · 2^(+CENTS/1200) / bin_hz )
centre= round( f / bin_hz )

lo    = clamp(lo, centre − MAX_BINS, centre)          MAX_BINS = 3
hi    = clamp(hi, centre,  centre + MAX_BINS)

energy[m] += weight[h] · max( spectrum[lo … hi] )     (inclusive range)
```

Harmonics whose centre bin falls below bin 1 or above Nyquist are skipped.

Four decisions in that block, each load-bearing:

**Tolerance in cents, not bins.** A fixed ±1-bin window is ±5.86 Hz at every
pitch — about a third of a semitone at C5, but nearly two semitones at G2. With
a fixed window the bass literally cannot separate neighbouring keys. Measured
on the synthetic corpus before the fix: a played G3 caused F#3 and G#3 to
register at ~80% of G3's own energy. A constant *ratio* keeps adjacent
semitones disjoint at every pitch.

**Edges rounded to nearest, not outward.** `floor`/`ceil` always widen the band
to at least two bins and usually three, re-creating the overlap the cents
window exists to remove.

**Capped at ±3 bins.** In the treble ±25 cents spans many bins, and each extra
bin is another chance for the peak-pick to grab noise. With 61 notes × 4
harmonics those pickups accumulate in the normalisation denominator and dilute
real peaks.

**Peak, not sum, within a band.** A partial occupies one or two bins; the rest
of the band is noise. Summing would reward wide bands for containing more noise.

Then fold to 12 pitch classes and L1-normalise both vectors:

```
chroma[p] = Σ  energy[m]     for all m where m mod 12 == p
chroma   /= Σ chroma                    (zero vector stays zero)
energy   /= Σ energy
```

L1 normalisation is what makes everything downstream **loudness-independent**:
the same chord played softly or loudly yields the same chroma, so one threshold
serves every dynamic with no gain stage.

### The overtone problem this does not fully solve

A piano C2 radiates strong energy at C3, G3, C4, E4. Weighting the fundamental
above the harmonics helps — a genuinely sounding note has energy at *its own*
fundamental, a merely-implied one usually does not — but leakage remains. It is
handled downstream by requiring the *absence* of loud unexpected pitch classes
(§4). Both mechanisms are required; neither is sufficient alone.

---

## 3. Onset detection — `core/onset.py`

Half-wave-rectified spectral flux between consecutive frames:

```
flux(t) = Σ_bins max(0, spectrum_t[b] − spectrum_{t−1}[b])
```

Rectified because a decaying note produces large *negative* deltas; counting
those would make one chord's release look like the next chord's attack.

Adaptive threshold over a rolling window of the last `ONSET_WINDOW_FRAMES` = 21
flux values (excluding the current frame, so a strong onset does not raise the
bar it must clear):

```
floor     = ONSET_FLUX_FLOOR · Σ spectrum_t          ONSET_FLUX_FLOOR = 0.10
threshold = max( ONSET_FLUX_RATIO · median(history), floor )
is_onset  = flux > threshold   AND   history is warm   AND   not refractory
```

**Warm-up.** No onset may be reported until `MIN_HISTORY_FRAMES` = 3 flux values
have been seen. Before that the only comparison available is the absolute
floor, and room tone clears it — which arms the verifier before a key is
touched and silently cancels the onset gate.

**Refractory.** After an onset, further onsets are suppressed for
`max(ONSET_REFRACTORY_MS, FFT_SIZE/HOP + 1 frames)`. This is forced by the
transform, not chosen: a 170 ms window slides over a hammer strike in four
42.7 ms hops and energy rises for all four, so one strike produces a *burst* of
consecutive high-flux frames. Unsuppressed, that burst repeatedly restarts the
stability clock. The floor must be derived from the window, because changing
`FFT_SIZE` changes the smear (at 16384 it is 341 ms).

**Why the floor is 10% and not lower.** The adaptive median collapses toward
zero during a long sustain, so the threshold becomes arbitrarily sensitive
exactly while a note is held. What it must reject there is not noise but
*beating*: equal-tempered intervals beat by construction — in C major, C4's 3rd
harmonic (784.9 Hz) against G4's 2nd (784.0 Hz) at ~0.9 Hz — and that swell is
genuine positive flux mid-chord. Measured: beating peaks at 2.2% of total
magnitude, an isolated strike hits 100%, and F major struck over a ringing
pedalled C major still reaches 50%.

**The detector is not reset between song steps.** Flux history describes the
audio stream, not the step. Clearing it on every advance would return the
detector to its warm-up state at every step of a song.

---

## 4. Verification — `core/verify.py`

State per step: `armed`, `held`, `latched`. Per frame, given `chroma`,
`energy`, `is_onset` and the frame's RMS:

```
if rms < SILENCE_RMS:                    # 0.005
    held = 0
    return not-confirmed                 # armed is PRESERVED, see below

if is_onset:
    if not armed:  armed = true
    else:          held  = 0             # a second strike restarts the clock

vector = OCTAVE_MODE ? energy : chroma
peak   = max(vector)

present = ∀ t ∈ target :   vector[t] ≥ PRESENT_THRESH · peak      # 0.35
extras  = { i ∉ target :   vector[i] >  EXTRA_MARGIN  · peak }    # 0.50

held      = (armed ∧ present ∧ extras = ∅) ? held + 1 : 0
confirmed = held ≥ STABILITY_FRAMES                               # ceil(250ms / 42.67ms) = 6
```

Once confirmed the verdict **latches** until `reset()`, so a step confirms once
and does not re-fire while the chord rings.

Thresholds are fractions of the frame's own peak rather than absolute values.
That is loudness-independent *and* chord-size-independent: a four-note chord
spreads normalised chroma thinner than a single note, and an absolute threshold
would silently get stricter as chords get denser.

**The deadband is deliberate.** `PRESENT_THRESH` (0.35) < `EXTRA_MARGIN` (0.50)
leaves a band where a pitch class is neither confidently present nor loud
enough to count as a wrong note. Overtone leakage lands there. Observed on a
synthetic C major: B reaches 0.064 against a 0.138 extra threshold — E's third
harmonic, correctly ignored.

**Silence preserves `armed`.** Clearing it would let the next chord confirm with
no strike of its own after any quiet gap — the exact failure onset gating
exists to prevent.

### The four conditions, and what dropping each one breaks

| Condition | Source | Failure if omitted |
|---|---|---|
| every target pitch present | PLAN step 4 | nothing detects |
| no unexpected pitch loud | PLAN step 5, decision A | overtones make everything read "correct" |
| an onset was seen | PLAN step 6, decision F | a ringing chord advances the song by itself |
| held for `STABILITY_MS` | PLAN step 6, decision B | a brushed key counts as a note |

### Two options, both off by default

`OCTAVE_MODE` (decision E) verifies exact MIDI notes against `energy` instead of
pitch classes against `chroma`. Stricter, and far more exposed to the overtone
problem — C4's 2nd harmonic *is* C5.

`HARMONIC_EXCLUSION` (decision A) exempts pitch classes that coincide with a
harmonic of a target from the extras check. Harmonic `h` sits `round(12·log₂h)`
semitones above its fundamental: the 2nd at +0, the 3rd at +7, the 4th at +0.
So it mainly forgives the fifth above a target. That helps — an A in a chord
radiates E, and a strict extras test would reject a correctly-played F major
for containing its own overtones — but it also means a genuinely wrong note a
fifth above a target goes unnoticed. Off by default so the unaided number is
measured first; `sweep.py` quantifies the trade.

---

## 5. Porting checklist

Work in this order; each step is independently verifiable against
`golden/golden.json`.

1. `hann_periodic(8)` matches `checks.hann_periodic_8`. Periodic, divisor N.
2. `midi_to_hz` matches `checks.midi_to_hz` for MIDI 36/48/60/69/84/96.
3. The band table matches `checks.bands` — a sample of `(midi, harmonic,
   lo_bin, hi_bin_exclusive)`. An off-by-one here is invisible in aggregate but
   shifts every chroma value.
4. `frames[*]` — decode `samples_b64_int16` (little-endian int16, ÷32767),
   compute chroma and note energies, compare to `expected` within `tolerance`
   (1e-6). Real corpus frames appear here once a corpus exists; the synthetic
   ones are always present.
5. `onset_sequence` — two frames of silence then a sustained C major triad.
   Exactly one onset, at the listed frame.
6. `verifier_trace` — 15 steps of hand-built chroma with no audio at all.
   Checks arming, the extras reset, stability counting and latching in
   isolation from the FFT.

Steps 1–5 test the transform; step 6 tests the logic. When a port fails, that
split tells you which half is wrong instead of leaving one opaque mismatch.

### Browser-specific notes

- `AudioContext` will usually be 48 kHz on macOS but is **not guaranteed** —
  read `audioContext.sampleRate` and rebuild the band table from it. Do not
  hardcode 48000.
- Prefer an `AudioWorklet` feeding raw frames to your own FFT over
  `AnalyserNode.getFloatFrequencyData`. The AnalyserNode applies its own
  smoothing and dB conversion, and its windowing is not under your control, so
  it cannot reproduce these vectors. `smoothingTimeConstant = 0` is necessary
  but not sufficient.
- Getting the mic flags wrong wrecks detection before any of this runs:
  `getUserMedia({audio: {echoCancellation: false, noiseSuppression: false,
  autoGainControl: false}})`. Automatic gain control in particular defeats the
  loudness-independence the L1 normalisation provides, because it changes the
  relative balance between partials over time rather than scaling them equally.
