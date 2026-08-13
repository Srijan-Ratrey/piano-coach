/**
 * Onset detection by spectral flux — the JS side of spike/core/onset.py.
 * See spike/ALGORITHM.md §3.
 *
 * Not optional. Without it a chord still ringing from the previous step
 * satisfies the next step for free and the song fast-forwards through itself
 * while the player sits there. The sustain pedal makes that worse by design.
 */

import { MIN_HISTORY_FRAMES, onsetRefractoryFrames } from './params.js';

/**
 * Sum of positive bin-to-bin magnitude increases.
 * Half-wave rectified: a decaying note produces large negative deltas, and
 * counting those would make one chord's release look like the next's attack.
 */
export function spectralFlux(spec, prev) {
  if (!prev || prev.length !== spec.length) return 0;
  let sum = 0;
  for (let i = 0; i < spec.length; i++) {
    const d = spec[i] - prev[i];
    if (d > 0) sum += d;
  }
  return sum;
}

export class OnsetDetector {
  constructor(params) {
    this.params = params;
    this.refractoryFrames = onsetRefractoryFrames(params);
    this._prev = null;
    this._history = [];
    this._sinceOnset = this.refractoryFrames;
    this.lastFlux = 0;
    this.lastThreshold = 0;
  }

  reset() {
    this._prev = null;
    this._history.length = 0;
    this._sinceOnset = this.refractoryFrames;
    this.lastFlux = 0;
    this.lastThreshold = 0;
  }

  /** Feed one magnitude spectrum; returns whether this frame is an onset. */
  step(spec) {
    this._sinceOnset++;

    const flux = spectralFlux(spec, this._prev);
    if (!this._prev || this._prev.length !== spec.length) {
      this._prev = new Float64Array(spec.length);
    }
    this._prev.set(spec);

    let total = 0;
    for (let i = 0; i < spec.length; i++) total += spec[i];
    const floor = this.params.onsetFluxFloor * total;

    // Median over history EXCLUDING the current frame, so a strong onset does
    // not raise the very bar it has to clear.
    const warmingUp = this._history.length < MIN_HISTORY_FRAMES;
    let threshold;
    if (warmingUp) {
      threshold = floor;
    } else {
      const sorted = Float64Array.from(this._history).sort();
      const mid = sorted.length >> 1;
      const median =
        sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
      threshold = Math.max(this.params.onsetFluxRatio * median, floor);
    }

    this._history.push(flux);
    if (this._history.length > this.params.onsetWindowFrames) this._history.shift();
    this.lastFlux = flux;
    this.lastThreshold = threshold;

    // No onsets until the passage has been observed. Otherwise the first
    // frames — where the only comparison is the absolute floor — report room
    // noise as a strike, arming the verifier before a key is touched.
    if (warmingUp) return false;
    if (flux <= threshold || flux <= 0) return false;

    // Refractory gate. History is still updated above, so a suppressed burst
    // continues to inform the median; only the reported onset is swallowed.
    if (this._sinceOnset < this.refractoryFrames) return false;

    this._sinceOnset = 0;
    return true;
  }
}
