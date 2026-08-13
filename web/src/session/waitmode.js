/**
 * The wait-mode loop — PLAN Feature 2's state machine.
 *
 *   show step -> WAIT -> (Layer 1 confirms) -> highlight -> advance -> next
 *
 * Scroll is frozen during WAIT and advances only on a confirmed match. Timing
 * and rhythm are not graded (DECISION #8): the player sets the pace entirely.
 */

import { Analyser, SlidingWindow } from '../audio/pipeline.js';
import { targetForStep } from '../song/midi.js';

export const WAITING = 'waiting';
export const CONFIRMED = 'confirmed';
export const FINISHED = 'finished';

export class WaitModeSession {
  /**
   * @param {object} song      parsed song with `steps`
   * @param {object} params    verification params (with the real sample rate)
   * @param {object} callbacks {onStep, onFrame, onFinish}
   */
  constructor(song, params, callbacks = {}) {
    this.song = song;
    this.params = params;
    this.callbacks = callbacks;

    this.analyser = new Analyser(params, null);
    this.window = new SlidingWindow(params, (frame) => this._onFrame(frame));

    this.stepIndex = -1;
    this.state = WAITING;
    this.confirmedCount = 0;
    this.startedAt = null;
    this.lastFrame = null;

    this.loop = null; // {from, to} step index range, or null
  }

  start() {
    this.startedAt = performance.now();
    this._goTo(0);
  }

  get currentStep() {
    return this.song.steps[this.stepIndex] ?? null;
  }

  /** Feed a hop-sized block of microphone audio. */
  pushAudio(block) {
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
   * Deliberately separate from `_advance('detected')` so the two never get
   * confused in the UI — a skipped step is not a played one.
   */
  skip() {
    this._advance('skipped');
  }

  back() {
    if (this.stepIndex > 0) this._goTo(this.stepIndex - 1);
  }

  restart() {
    this.confirmedCount = 0;
    this._goTo(0);
  }

  setLoop(from, to) {
    this.loop = from === null ? null : { from, to };
  }

  _advance(reason) {
    const step = this.currentStep;
    if (!step) return;

    if (reason === 'detected') this.confirmedCount++;
    this.callbacks.onConfirm?.(step, reason);

    let next = this.stepIndex + 1;
    if (this.loop && next > this.loop.to) next = this.loop.from;

    if (next >= this.song.steps.length) {
      this.state = FINISHED;
      this.callbacks.onFinish?.({
        steps: this.song.steps.length,
        confirmed: this.confirmedCount,
        elapsedMs: performance.now() - this.startedAt,
      });
      return;
    }
    this._goTo(next);
  }

  _goTo(index) {
    this.stepIndex = index;
    this.state = WAITING;
    const step = this.currentStep;
    if (!step) return;

    const target = targetForStep(step, this.params);
    // A step whose notes all fall outside the analysed range cannot be
    // verified. Rather than hanging on it forever, hand it to the UI as
    // unverifiable so the player can skip past it knowingly.
    this.analyser.setTarget(target.length ? target : null);
    this.callbacks.onStep?.(step, { verifiable: target.length > 0 });
  }
}
