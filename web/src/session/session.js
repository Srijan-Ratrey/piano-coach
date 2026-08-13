/**
 * The practice session — PLAN Feature 2's state machine, in two modes.
 *
 *   WAIT:  show step -> freeze -> (Layer 1 confirms) -> advance -> next
 *   PLAY:  the clock runs; notes pass whether or not they were played
 *
 * ## One clock
 *
 * Both modes are the same mechanism. Song time advances at a constant rate
 * scaled by tempo, and the only difference is whether that clock is clamped at
 * the current step:
 *
 *     songTime += dt * tempo
 *     WAIT:  songTime = min(songTime, currentStep.time)
 *     PLAY:  unclamped
 *
 * Two separate clocks would drift apart the moment either mode was touched, so
 * there is deliberately only one.
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

    this.songTime += dt * this.tempo;

    if (this.mode === WAIT) {
      const step = this.currentStep;
      if (step) this.songTime = Math.min(this.songTime, step.time);
      return;
    }

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
    if (rewind) this.songTime = step.time - LEAD_IN_SECONDS;
    if (this.mode === WAIT) this.songTime = Math.min(this.songTime, step.time);

    const target = targetForStep(step, this.params);
    // A step whose notes all fall outside the analysed range cannot be
    // verified. Rather than hanging on it forever, hand it to the UI as
    // unverifiable so the player can skip past it knowingly.
    this.analyser.setTarget(target.length ? target : null);
    this.callbacks.onStep?.(step, { verifiable: target.length > 0 });
  }
}
