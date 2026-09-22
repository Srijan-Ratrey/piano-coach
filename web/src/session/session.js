/**
 * The practice session — PLAN Feature 2's state machine, in two modes.
 *
 *   WAIT:  show step -> freeze -> (Layer 1 confirms) -> advance -> next
 *   PLAY:  the clock runs; notes pass whether or not they were played
 *
 * ## One clock
 *
 * Both modes run one clock at `dt * tempo`, and differ only in what happens at
 * the current step:
 *
 *     PLAY:  unclamped — the clock is the music, exactly
 *     WAIT:  eased across the gap, coming to rest on the line (`_waitScroll`)
 *
 * Two separate clocks would drift apart the moment either mode was touched, so
 * there is deliberately only one. WAIT spends the same `span / tempo` seconds
 * crossing a gap that PLAY would, so the two agree on rhythm and differ only in
 * how the motion is distributed inside a gap.
 *
 * ## Why the session owns the clock rather than the renderer
 *
 * The renderer used to own scroll position and ease it toward the current step
 * with a fixed time constant. That made every transition take about the same
 * time regardless of the musical gap, so the motion carried no rhythm. Rate
 * through *song time* is the thing tempo scales, and it belongs next to the
 * step logic, not in the drawing code.
 *
 * DECISION #8 (advance only on a confirmed match, never on a clock) governs
 * WAIT mode, which remains the default. PLAY is an opt-in practice mode that
 * explicitly does not gate — see DECISIONS #20.
 */

import { Analyser, SlidingWindow } from '../audio/pipeline.js';
import { targetForStep } from '../song/midi.js';

export const WAIT = 'wait';
export const PLAY = 'play';

export const FINISHED = 'finished';

/** Song-seconds of run-up before the first note, so it glides in rather than
 *  starting already at the line. Doubles as a count-in in PLAY mode. */
export const LEAD_IN_SECONDS = 2.0;

/**
 * How long a step stays current after its due time in PLAY mode before it
 * counts as missed. Comparable to the 250 ms stability window — a note played
 * fractionally late should still register against the step it belongs to, not
 * the next one.
 */
export const GRACE_SECONDS = 0.35;

export const MIN_TEMPO = 0.25;
export const MAX_TEMPO = 1.5;

export class PracticeSession {
  /**
   * @param {object} song      parsed song with `steps`
   * @param {object} params    verification params (with the real sample rate)
   * @param {object} callbacks {onStep, onFrame, onConfirm, onMiss, onFinish}
   */
  constructor(song, params, callbacks = {}) {
    this.song = song;
    this.params = params;
    this.callbacks = callbacks;

    this.analyser = new Analyser(params, null);
    this.window = new SlidingWindow(params, (frame) => this._onFrame(frame));

    this.stepIndex = -1;
    this.state = WAIT;
    this.mode = WAIT;
    this.tempo = 1.0;
    this.paused = false;
    this.confirmedCount = 0;
    this.missedCount = 0;
    this.finished = false;
    this.startedAt = null;
    this.lastFrame = null;
    this.songTime = 0;
    // WAIT-mode travel: where the current leg started, how much musical time
    // has been spent on it, the speed it began at, and the speed right now.
    this.travelFrom = 0;
    this.travelled = 0;
    this.entrySlope = 0;
    this.rate = 0;

    this.loop = null; // {from, to} step index range, or null
  }

  start() {
    this.startedAt = performance.now();
    this.songTime = (this.song.steps[0]?.time ?? 0) - LEAD_IN_SECONDS;
    this._goTo(0);
  }

  get currentStep() {
    return this.song.steps[this.stepIndex] ?? null;
  }

  setTempo(tempo) {
    this.tempo = Math.min(MAX_TEMPO, Math.max(MIN_TEMPO, tempo));
  }

  /**
   * How long a match must hold before it counts (open decision B).
   *
   * Exposed to the player because it is the dominant term in perceived
   * latency — measured at 175 ms of a 259 ms strike-to-confirm — and because
   * the right value is hardware-dependent, which DECISIONS says plainly. Until
   * a corpus has been recorded on the actual piano, the player finding their
   * own point beats a number picked blind.
   *
   * Only verification fields change here, never transform fields, so the
   * chroma extractor and its band table stay valid. The verifier is rebuilt on
   * the next `setTarget`, which is why the current step is re-armed.
   */
  setResponseMs(stabilityMs) {
    this.params = { ...this.params, stabilityMs };
    this.analyser.params = this.params;
    const step = this.currentStep;
    if (step) {
      const target = targetForStep(step, this.params);
      this.analyser.setTarget(target.length ? target : null);
    }
  }

  setMode(mode) {
    this.mode = mode === PLAY ? PLAY : WAIT;
    // Leaving PLAY mid-flight can strand the clock past the current step, which
    // in WAIT mode would render as a note already below the line. Pull it back.
    if (this.mode === WAIT && this.currentStep) {
      this.songTime = Math.min(this.songTime, this.currentStep.time);
      this.travelFrom = this.songTime;
      this.travelled = 0;
      this.entrySlope = 0;
    }
  }

  /**
   * Advance the clock. `dt` is real seconds.
   *
   * Callers must clamp `dt` — requestAnimationFrame stops in a background tab,
   * so the first frame after returning reports however long the tab was hidden
   * and would otherwise jump the clock by that much.
   */
  tick(dt) {
    if (this.finished || this.paused || dt <= 0) return;

    if (this.mode === WAIT) {
      this._waitScroll(dt);
      return;
    }

    this.songTime += dt * this.tempo;

    // PLAY: steps fall behind the clock whether or not they were played.
    // Guarded by a step count rather than `while (true)` so a pathological
    // song cannot spin here.
    for (let guard = 0; guard < this.song.steps.length + 1; guard++) {
      const step = this.currentStep;
      if (!step) break;
      if (this.songTime <= this._passTime(step)) break;
      this._advance('missed');
    }
  }

  /**
   * WAIT mode's scroll: eased across the gap rather than clamped at the end.
   *
   * This clock is pure animation — the verifier advances steps, never the
   * clock — so its shape is free to serve the eye without touching musical
   * timing or detection. It needed shaping: clamping the position with `min()`
   * gave the scroll a square-wave velocity, full tempo to a standstill and back
   * in a single frame roughly twice a second, which reads as stutter however
   * steady the frame rate is. PLAY's clock is the music and is left exact.
   *
   * The easing is applied to *progress through the gap*, not to the rate, and
   * that distinction is the whole point. Ramping the rate up and down would add
   * its ramp time to every gap — a fixed cost that compresses the contrast
   * between a sixteenth and a whole note, which is the same way the old
   * fixed-time-constant renderer destroyed rhythm. Easing progress spends
   * exactly `span / tempo` seconds whatever the span, so it comes to rest on
   * the line and still takes twice as long over twice the distance.
   *
   * The curve is the cubic through `h(0)=0, h(1)=1, h'(1)=0` that leaves the
   * start slope free, because a leg does not always begin at rest: a step
   * confirmed while its bar is still falling — routine in a fast passage, where
   * the previous note's detection latency lands mid-travel — starts the next
   * leg at whatever speed the scroll already had. Forcing those to zero would
   * put back a step change at exactly the tempo where it is most visible. With
   * `a = 0` this is plain smoothstep.
   */
  _waitScroll(dt) {
    const step = this.currentStep;
    if (!step) {
      this.songTime += dt * this.tempo;
      return;
    }
    const span = step.time - this.travelFrom;
    if (span <= 0) {
      this.songTime = step.time;
      this.rate = 0;
      return;
    }
    this.travelled += dt * this.tempo;
    const u = Math.min(1, this.travelled / span);
    const a = this.entrySlope;
    this.songTime = this.travelFrom + span * (a * u + (3 - 2 * a) * u * u + (a - 2) * u * u * u);
    // h'(u) factored as (1 - u)(a + (6 - 3a)u): zero at the line by
    // construction, and non-negative for a <= 3, which is why entry is capped
    // there — a faster entry would overshoot the line and scroll backwards.
    this.rate = this.tempo * (1 - u) * (a + (6 - 3 * a) * u);
  }

  /**
   * When the current step stops accepting input in PLAY mode: its due time plus
   * the grace window, or the next step's due time, whichever comes first —
   * otherwise a fast passage would be swallowed by the grace of the note before
   * it.
   */
  _passTime(step) {
    const next = this.song.steps[step.index + 1];
    const graceEnd = step.time + GRACE_SECONDS;
    return next ? Math.min(graceEnd, next.time) : graceEnd;
  }

  /** Feed a hop-sized block of microphone audio. */
  pushAudio(block) {
    if (this.paused) return;
    this.window.push(block);
  }

  _onFrame(frame) {
    const result = this.analyser.push(frame);
    this.lastFrame = result;
    this.callbacks.onFrame?.(result);

    if (result.verdict?.confirmedNow) {
      this._advance('detected');
    }
    return result;
  }

  /**
   * Move past the current step without detecting it.
   *
   * A practice aid, and the only way to exercise the roll without a piano.
   * Deliberately distinct from `_advance('detected')` so the two never get
   * confused — a skipped step is not a played one.
   */
  skip() {
    this._advance('skipped');
  }

  togglePause() {
    this.paused = !this.paused;
    return this.paused;
  }

  back() {
    if (this.stepIndex > 0) this._goTo(this.stepIndex - 1, { rewind: true });
  }

  restart() {
    this.confirmedCount = 0;
    this.missedCount = 0;
    this.finished = false;
    this.songTime = (this.song.steps[0]?.time ?? 0) - LEAD_IN_SECONDS;
    this._goTo(0, { rewind: true });
  }

  setLoop(from, to) {
    this.loop = from === null ? null : { from, to };
  }

  _advance(reason) {
    const step = this.currentStep;
    if (!step) return;

    if (reason === 'detected') {
      this.confirmedCount++;
      this.callbacks.onConfirm?.(step, reason);
    } else if (reason === 'missed') {
      this.missedCount++;
      this.callbacks.onMiss?.(step);
    } else {
      this.callbacks.onConfirm?.(step, reason);
    }

    let next = this.stepIndex + 1;
    let wrapped = false;
    if (this.loop && next > this.loop.to) {
      next = this.loop.from;
      wrapped = true;
    }

    if (next >= this.song.steps.length) {
      this.finished = true;
      this.state = FINISHED;
      this.callbacks.onFinish?.({
        steps: this.song.steps.length,
        confirmed: this.confirmedCount,
        missed: this.missedCount,
        elapsedMs: performance.now() - this.startedAt,
      });
      return;
    }
    this._goTo(next, { rewind: wrapped });
  }

  _goTo(index, { rewind = false } = {}) {
    this.stepIndex = index;
    this.state = WAIT;
    const step = this.currentStep;
    if (!step) return;

    // A rewind is a deliberate navigation, so the clock jumps rather than
    // travelling backwards through the song at tempo.
    // A rewind is a jump, so no velocity carries across it.
    if (rewind) {
      this.songTime = step.time - LEAD_IN_SECONDS;
      this.rate = 0;
    }
    if (this.mode === WAIT) this.songTime = Math.min(this.songTime, step.time);
    this.travelFrom = this.songTime;
    this.travelled = 0;
    this.entrySlope = Math.min(3, this.rate / this.tempo);

    const target = targetForStep(step, this.params);
    // A step whose notes all fall outside the analysed range cannot be
    // verified. Rather than hanging on it forever, hand it to the UI as
    // unverifiable so the player can skip past it knowingly.
    this.analyser.setTarget(target.length ? target : null);
    this.callbacks.onStep?.(step, { verifiable: target.length > 0 });
  }
}
