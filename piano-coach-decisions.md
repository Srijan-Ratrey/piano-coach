# DECISIONS

Project: **`piano-coach`** — a wait-mode piano learning **web page** (runs in the browser; listens to a real piano via microphone and waits for the correct notes before advancing). No native app, no install. Rename freely; this file is the record of what's decided and why.

Inspired by the wait-mode pattern (notes hold at the keyboard line until played). This is an independent implementation, not a fork.

---

## Locked decisions

| # | Decision | Choice | Why / consequence |
|---|---|---|---|
| 1 | **Input** | Microphone, full polyphony (real acoustic/any piano) | Matches the reference behavior ("play on a real piano; screen keys are only a guide"). The hard path — the whole plan is built to make it tractable. No MIDI hardware assumed. |
| 2 | **Core detection model** | **Verification, not transcription** | In wait-mode the app already knows the next notes. It checks "are these known pitches present?" rather than "what is being played?" This is the single decision that makes mic-polyphony feasible. |
| 3 | **Latency stance** | Tolerant (~300–700 ms to confirm) | A held note rings ~1 s. No real-time transcription needed. Progression is player-driven, not clock-driven. |
| 4 | **Detection tech (Layer 1)** | Web Audio FFT → chroma + harmonic-presence check. **No ML.** | Runs in the browser in real time with zero model download. Drives the whole wait-mode loop. |
| 5 | **Free-play recognition (Layer 2)** | Deferred; when built, use basic-pitch or Magenta Onsets & Frames on sliding windows | Only needed for an optional "what am I playing?" mode. Not on the v1 path. |
| 6 | **Song format** | MIDI files (`@tonejs/midi`) | "Any song" = any song you have/can find a MIDI for. Arbitrary MP3→notes is Layer-2-grade and imperfect; out of v1 scope. |
| 7 | **Rendering** | Plain Canvas, landscape piano-roll. ~~Vertical keyboard on one edge, bars scrolling toward it~~ → **superseded by #19** | No framework needed. |
| 8 | **Progression rule** | Advance only when Layer 1 confirms the target notes; ignore timing/rhythm | Confirmed reference behavior: it waits for correct notes, does not grade rhythm in this mode. |
| 9 | **On-screen keyboard** | Guide only — highlight target keys, never accept taps as input | Input is always the mic. Screen keys are visual scaffolding. |
| 10 | **Delivery** | A **web page** — single-page web app, everything client-side, no install | Layer 1 runs entirely in the browser (Web Audio + Canvas). Works on desktop and mobile browsers. Optional PWA later for an installable/offline feel. |
| 11 | **Hosting** | Static host over **HTTPS** (any static host, or existing Cloudflare setup) | HTTPS is mandatory — `getUserMedia` (mic) is blocked on plain HTTP except `localhost`. No backend for v1. |
| 12 | **Go/no-go gate** | A validation spike before any UI | Everything assumes chroma-verification clears ~90% on the user's own piano+mic. Prove that first. |
| 13 | **Name** | `piano-coach` | Matches the git remote (`Srijan-Ratrey/piano-coach`) and the filenames of these documents. The working directory stays `piano`. |
| 14 | **Spike language** | **Python + numpy** | Offline DSP tuning — threshold sweeps, spectrograms, scoring a corpus — is what numpy is for. Iterating on thresholds inside a browser would be far slower. |
| 15 | **App language** | **Unchanged: browser JS** | #10 and #11 stand. The spike is a measuring instrument, not the product; it is throwaway by design. |
| 16 | **Port discipline** | Plain numpy only (no scipy/librosa) + a written algorithm spec + golden acceptance vectors | The cost of #14 is writing the DSP twice, and the risk is a port that drifts — in which case the gate measured nothing. `spike/ALGORITHM.md` pins the maths and `spike/golden/golden.json` pins the numbers, split into transform tests and state-machine tests so a failure localises. |
| 17 | **Spike hardware** | Digital piano through its speakers → mic | The recorder stamps the device into `manifest.json`. Cleaner harmonics than an acoustic instrument, so results are optimistic for the acoustic case. |
| 20 | **Motion is tempo-paced; a play-along mode exists** | Song time advances at a constant rate scaled by a tempo % (25–150, default 100). **WAIT mode** clamps that clock at the current step — it still advances only on a confirmed match, so **#8 is unchanged and remains the default**. **PLAY mode** leaves the clock unclamped: notes pass whether or not they were played, and a missed note is visual feedback only — no score, no penalty, still no timing grade. | The roll previously eased toward the next step with a fixed ~91 ms time constant, so every transition took the same time regardless of the musical gap and the motion carried no rhythm at all. Rate through *song time* is what tempo scales, so pacing moved out of the renderer and into the session. Both modes are deliberately **one clock with one conditional clamp** rather than two code paths — two clocks would drift apart the first time either was touched. Play-along is opt-in and does not gate, so it does not contradict #8; it sits beside it. |
| 19 | **Piano-roll orientation** | **Keyboard horizontal along the bottom, bars falling top-to-bottom onto it.** Supersedes the sideways layout in #7. | Changed after seeing the first build. The sideways reading of PLAN ("keyboard drawn vertically on one edge; note bars scroll toward it") was implemented and rejected on sight: falling-onto-a-bottom-keyboard is what every player already recognises, and it maps directly onto the physical instrument in front of them — left is low, right is high, exactly as their hands are arranged. The sideways version required a mental rotation before the guidance was usable. Purely a rendering change: `render/keyboard.js` and `render/pianoroll.js`, no effect on detection, MIDI parsing or the wait-mode loop. Session controls moved to the top of the screen, since a bar floating over the bottom now covers the keys. |
| 18 | **Build order deviation** | The browser app (PLAN steps 5–6) was built **before** the corpus was recorded | A deliberate departure from #12, made at the user's direction. The renderer and MIDI path do not depend on the gate's outcome and stay useful even under a NO-GO — a pivot to MIDI input would need the same piano-roll. What *is* affected: the app currently runs on the spike's untuned default thresholds, so microphone detection quality is unknown rather than measured. Recording the corpus and copying the swept constants into `web/src/audio/params.js` closes the gap. |

### Browser platform decisions
| # | Decision | Choice | Why |
|---|---|---|---|
| B1 | Mic access | `getUserMedia({audio})` after a user gesture | Browser requires HTTPS + an explicit user action; show a "Start / enable mic" button, don't auto-request on load. |
| B2 | Audio start | Create/resume `AudioContext` inside a click/tap handler | Browsers suspend `AudioContext` until a user gesture; resume on the same button that grants the mic. |
| B3 | Orientation | Landscape; prompt to rotate on portrait phones | The piano-roll is wide. Desktop is fine; on a phone browser show a "rotate your device" hint (can't force rotation from the web). |
| B4 | Target browsers | Chromium-based + Safari/Firefox current | Web Audio is universal; test Safari/iOS separately (stricter autoplay + mic quirks). |

---

## Feature-level decisions confirmed from the reference

| Aspect | Decision |
|---|---|
| Two hands | Two colored streams (e.g. orange = right, blue = left). |
| Per-hand difficulty | Set before the session; difficulty **thins the notes** shown for that hand (Easy = melody/fewer → full). |
| Finger numbers | Printed on each note bar as guidance. **Display only — not verified** (mic can't detect fingers). |
| Tempo control | Tempo % scaler for play-along pacing. Not used for scoring. |
| Section loops | Pick a start/end step range and repeat it in wait-mode. |
| Note names | Reference marker (e.g. C4) at the keyboard. |

---

## Still open — decide during build

**A, B and E are now decidable rather than arguable.** `spike/sweep.py` runs the
recorded corpus at every combination of these settings and reports the
recall / false-confirm / latency frontier; `spike/RESULTS.md` records which one
the recording supports. Until the corpus exists they stay open, but they are no
longer matters of preference. F is settled — see below.

### A. Extra-note strictness
When verifying a target chord, how hard do you reject *extra* pitches (overtones, neighbouring keys)?
→ *Recommendation:* require all target pitch-classes present AND no unexpected pitch-class louder than a margin. Tune the margin during the spike.

### B. Stability window — **interim value 175 ms; still open**
How many consecutive frames must the target hold before advancing?
→ *Recommendation:* ~200–300 ms. Long enough to reject a brushed key, short enough to feel responsive. Make it a constant to tune.
→ **Reported as laggy in use, so it was measured.** Strike-to-confirmation through the JS port was **323 ms**, and the breakdown put the blame squarely here: the onset was detected at just **+24 ms**, so the 250 ms hold was ~77% of the delay. Two changes brought it to **259 ms** (C4) / **195 ms** (C6):

- `HOP` 2048 → 1024. Pure win, no accuracy trade — the hop is a quantisation error on every verdict, since a confirmation can only be reported on a frame boundary. Costs ~8% of one core (a frame is 0.178 ms measured).
- `STABILITY_MS` 250 → 175. Scoring the synthetic corpus from 100–300 ms showed recall and false-confirms **completely flat** while latency scaled linearly.

→ **Why this stays open.** That corpus evidence is weak in a specific way: synthetic tones have almost no attack transient, and rejecting transients is exactly what this window is for, so the test is close to blind to the cost of shortening it. 175 ms is a deliberate step back from the measured-free 100 ms. The real corpus settles it. Meanwhile the session exposes a **Response** control (Fast 125 / Balanced 175 / Careful 250) — the right value is hardware-dependent, and letting the player find their own point beats a number picked blind.

→ Note the remaining ~60 ms before the hold even begins: the 171 ms analysis window has to fill with enough of the note for the chroma to be dominated by it. Only a smaller `FFT_SIZE` would cut that, and it would cost bass resolution, which is already the weakest register.

### C. Single-hand vs two-hand for v1
→ *Recommendation:* single-hand first (one stream, one verification target). Add the two-hand overlay in v2 once verification is solid.

### D. Note vs chord as the "step" unit
Songs mix single notes and chords. Is a "step" one simultaneous group, or one note?
→ *Recommendation:* group notes within a small time window (e.g. 40–60 ms) into one step; verify the whole group.

### E. Octave handling
A low note's overtones mimic higher notes. Do you verify exact octave, or pitch-class only?
→ *Recommendation:* start pitch-class only (more robust on cheap mics), tighten to octave if the spike shows it's reliable.

### F. Chord onset vs sustain — **SETTLED: yes, gate on onset**
Sustain pedal blurs consecutive chords. Require an energy *rise* (onset) to accept a new step, not just presence.
→ **Implemented** in `spike/core/onset.py` as half-wave-rectified spectral flux against an adaptive median. Building it surfaced three requirements that were not obvious from the recommendation, all of which are load-bearing rather than polish:

- **A refractory gate.** A single strike produces a *burst* of consecutive high-flux frames, because a 170 ms analysis window slides over the attack in four 42.7 ms hops and energy rises for all four. Unsuppressed, that burst repeatedly restarts the stability clock. The refractory floor has to be derived from the window size, not fixed in milliseconds, since changing the FFT size changes the smear.
- **A warm-up period.** With no history to compare against, the first frames report room tone as a strike — arming the verifier before a key is touched and silently cancelling the gate. Related: the onset detector must *not* be reset when the song advances a step, or every step re-enters that state.
- **A meaningful flux floor.** The adaptive median collapses toward zero during a sustain, so the threshold becomes arbitrarily sensitive exactly while a note is held. What it has to reject there is not noise but *beating* — equal-tempered intervals beat by construction, and that swell is genuine positive flux mid-chord.

### G. Framework / stack — **SETTLED: as recommended**
→ **Built** in `web/`: vanilla JS, Web Audio, Canvas, `@tonejs/midi`, Vite as the dev server and bundler. Two runtime dependencies in total. Tone.js playback of passed notes is not built and remains optional.
→ *Note:* the spike (#14) is Python, which does not affect this. See #15/#16.
→ Three browser details that cost real debugging time and are easy to undo by accident: the AudioWorklet is served verbatim from `public/` rather than imported (bundlers inline small files as `data:` URLs, which `addModule()` accepts inconsistently); `AnalyserNode` is unusable here because its smoothing and windowing cannot reproduce the golden vectors; and the analysis sample rate is read from the live `AudioContext` rather than assumed to be 48 kHz.

### H. Chroma band width — **SETTLED: constant cents, not constant bins**
Not in the original list; found while building. How wide a frequency band should each harmonic be searched over?
→ **±25 cents, capped at ±3 bins**, edges rounded to nearest. A fixed ±1-*bin* window is ±5.86 Hz at every pitch — a third of a semitone at C5 but nearly two semitones at G2 — so in the bass, neighbouring keys read each other's energy directly. Measured on synthetic audio before the fix: a played G3 caused F#3 and G#3 to register at ~80% of G3's own strength. A constant *ratio* keeps adjacent semitones disjoint at every pitch.

---

## Hard limits to state up front (in the README)

- **No finger detection from mic.** Finger numbers are shown, never verified.
- **"Any song" = any MIDI you can obtain.** Not arbitrary audio.
- **Feasibility is hardware-dependent.** The validation spike on the user's actual piano + mic is the real gate; don't build the UI on faith.
- **Harmonic/octave ambiguity is the core technical risk.** Verification survives it better than transcription, but per-note harmonic weighting and extra-note rejection are mandatory, not optional.