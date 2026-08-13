# PLAN

Project: **`piano-coach`** — a wait-mode piano learning **web page** (runs in the browser, no install). Listens to a real piano through the microphone, shows falling notes, and waits for the correct notes before advancing.

**Two features:** (1) recognize the keys/chords played via microphone (full polyphony); (2) play any song of your choice with a wait-mode learning loop. See `DECISIONS.md` for locked choices.

Everything runs client-side — it's a static single-page site served over HTTPS. No backend for v1.

---

## The insight the whole plan rests on

**In wait-mode you verify, not transcribe.**

Blind "listen and identify any chord from a mic" is a research problem. But in wait-mode the app already knows the next notes from the song file. The real question is not *"what did they play?"* — it's *"are these specific known notes present in the sound right now?"*

Consequences:
- **Latency stops mattering** — a held chord rings ~1 s; 300–700 ms to confirm is fine.
- **Detection becomes targeted** — check for energy at the expected fundamentals/harmonics of the known target notes. Real-time, plain FFT, no ML.

This is what turns the mic-polyphony path from a months-long wall into something buildable.

---

## Architecture — two detection layers

```
   mic ─▶ Web Audio (AnalyserNode / FFT) ─▶ chroma + harmonic energy
                                              │
             ┌────────────────────────────────┴───────────────────────────┐
        LAYER 1: VERIFICATION                 LAYER 2: FREE RECOGNITION
        (wait-mode — the product, v1)         ("what am I playing?" — optional, later)
        • target notes known                  • target unknown
        • FFT → are target pitches present?    • basic-pitch OR Onsets & Frames
        • real-time, no ML                     • sliding ~1 s window, ~0.5–1 s lag
        • drives advance + key highlight       • only for a free-play mode
```

Layer 1 is ~90% of the app and needs no ML. Build it first. Layer 2 is a bolt-on.

---

## Browser constraints (get these right first — they silently break mic apps)

- **HTTPS is mandatory.** `getUserMedia` (mic) is blocked on plain HTTP everywhere except `localhost`. Deploy to an HTTPS static host; develop on `localhost`.
- **Mic needs a user gesture.** Don't request the mic on page load. Show a "Start / enable microphone" button and call `getUserMedia({audio:{echoCancellation:false, noiseSuppression:false, autoGainControl:false}})` inside the click — those three flags OFF, or the browser will "clean" your piano signal and wreck detection.
- **AudioContext starts suspended.** Create/`resume()` it in the same gesture. Until then it produces silence.
- **Landscape.** The piano-roll is wide; desktop is ideal. On a phone browser you can't force rotation — detect portrait and show a "rotate your device" hint.
- **Safari/iOS quirks.** Stricter autoplay + mic handling; test it as its own target, not an afterthought.
- **Tab backgrounding.** Browsers throttle timers/audio in background tabs — pause the session cleanly on `visibilitychange`.

## Feature 1 — key/chord recognition (Layer 1)

**Per audio frame (~every 50 ms):**
1. `getUserMedia` audio → `AudioContext` → `AnalyserNode` (FFT ~8192 for low-frequency resolution).
2. Read the frequency-magnitude spectrum.
3. Build a **12-bin chroma vector**: for each pitch class, sum energy at its fundamentals across octaves.
4. **Verify** against the known target step: each target pitch-class must exceed an adaptive, loudness-independent threshold.
5. **Reject extras**: no unexpected pitch-class louder than a margin (kills overtone/neighbour false-positives).
6. **Require onset + stability**: energy must rise (new strike, not sustain bleed) and hold ~200–300 ms.
7. All conditions met → **matched** → advance the song step.

**Known hard cases (handle explicitly):**
- Harmonic/octave ambiguity — a low note's overtones mimic higher notes. Weight fundamentals over overtones; start pitch-class-only, tighten to octave only if reliable.
- Sustain pedal / reverb — require an onset rise per step.
- Mic quality + the specific piano decide feasibility → see the validation spike.

---

## Feature 2 — play any song (the wait-mode loop)

**A song = a MIDI file.**
1. Load `.mid` → parse with `@tonejs/midi` → `{ note, startTime, duration, track, finger? }`.
2. Group near-simultaneous notes (~40–60 ms window) into **steps** (one note or chord to play).
3. Render the **landscape piano-roll**: keyboard laid out horizontally along the bottom edge; note bars fall onto it; the bar at the line is the current step. (Originally specified as a sideways keyboard on one edge — see DECISIONS #19 for why that changed.)
4. Optionally play already-passed notes via a sampled piano (Tone.js Sampler + soundfont) so the song is audible as it builds.

**Wait-mode state machine:**
```
   show step ─▶ WAIT ──(Layer 1 confirms target notes)──▶ highlight matched key ─▶ advance ─▶ next step
                  ▲                                                                             │
                  └──────────────────────── repeat until song ends ──────────────────────────────┘
```
- Scroll frozen during WAIT; advances only on a confirmed match.
- Tempo % and section-loop controls pace the session; they do not grade timing.

**Renderer details (from the reference):**
- Two colored streams: right hand vs left hand.
- Finger numbers printed on bars (from MIDI fingering or a simple heuristic) — display only, not verified.
- Per-hand difficulty = filter which of that hand's notes are shown (Easy = fewer → full).
- Section loop = repeat a chosen start/end step range in wait-mode.
- On-screen keyboard highlights target keys — a guide only; never an input.

---

## Screens (from the reference flow)

These are **routes/views in one single-page web app** (hash or history routing), not native screens.

1. **Landing** — one-line pitch + a single **"Enable microphone & start"** button (this is the required user gesture: grants mic + resumes AudioContext).
2. **Library** — "Recently played" + category filters (All, Classical, Evergreens, Kids Songs, Famous Intros, Movie Themes, Recent Hits); search / favourite. ("Download" becomes local-favourites in a web build — persist via `localStorage`.)
3. **Song detail** — cover, tags, per-hand difficulty selectors, "Start the session."
4. **Session** — the wait-mode piano-roll + tempo / metronome / loop / quit controls; portrait phones get a "rotate to landscape" hint.

v1 can ship with a minimal Landing → pick a MIDI → Session, and grow the library UI later.

**Optional later:** wrap as a **PWA** (manifest + service worker) so it's installable and works offline — this is the closest a web page gets to the native-app feel, without leaving the browser.

---

## Build order (each step independently testable)

1. **VALIDATION SPIKE — do this before any UI.** Record ~10 known chords on the actual piano + mic. Offline, run the chroma-verification + extra-note-rejection logic and score: were target pitch-classes found, and were false extras avoided? Target ~90%. If ~60%, you've learned the ceiling cheaply.
2. **Live chroma meter** — mic → real-time 12-bin display. Play a note, watch the right bin light. Proves the audio pipeline.
3. **Single-note verification** — "play a C" → detect → advance. Whole loop, one note.
4. **Chord verification** — multi-note targets with overlap/extra-note/onset guards.
5. **MIDI loader + piano-roll** — parse a MIDI, render falling bars, manual advance. Visuals only.
6. **Wire the loop** — connect step 4 detection to step 5 advance = wait-mode end to end, single hand.
7. **v2 polish** — two-hand colored streams, per-hand difficulty, finger numbers, tempo scaling, section loops, on-screen key highlight, library UI.
8. **Layer 2 (optional)** — basic-pitch / Onsets & Frames free-play mode.

---

## Tech stack

- **Audio / analysis:** Web Audio API (`AnalyserNode`, FFT) — Layer 1, no dependency.
- **Song parsing:** `@tonejs/midi`.
- **Playback (optional):** Tone.js Sampler + piano soundfont.
- **Rendering:** Canvas.
- **Layer 2 (optional):** Spotify basic-pitch (TF.js) or Magenta Onsets & Frames.
- **Delivery:** single-page web app, static files, served over **HTTPS**. Optional PWA (manifest + service worker) for install/offline.
- **Persistence:** `localStorage` for favourites/recent/settings (no backend).

---

## Repo layout (suggested)

```
piano-coach/
├─ README.md
├─ DECISIONS.md
├─ PLAN.md
├─ src/
│  ├─ audio/          # mic capture, FFT, chroma, verification (Layer 1)
│  ├─ song/           # MIDI parse, step grouping
│  ├─ render/         # piano-roll canvas, keyboard, note bars
│  ├─ session/        # wait-mode state machine, loop/tempo controls
│  └─ ui/             # home, library, song detail
├─ spike/             # standalone validation-spike script + sample recordings
└─ public/            # index.html, sample MIDIs, soundfont, (optional) manifest.webmanifest + sw.js
```

---

## Risks / honest gotchas

- **The validation spike is the real go/no-go.** Everything downstream assumes chroma-verification clears ~90% on the target hardware. Prove it before building the UI.
- **Extra-note rejection matters as much as target detection** — without it, overtones/neighbours make everything read "correct."
- **Onset gating** is required so a still-ringing chord doesn't auto-satisfy the next step.
- **No finger detection from mic** — finger numbers are shown, never verified. State this in the README.
- **"Any song" = any MIDI you can obtain**, not arbitrary audio.