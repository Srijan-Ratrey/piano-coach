/**
 * Wires dsp -> chroma -> onset -> verify, mirroring spike/core/pipeline.py.
 *
 * Same structure as the Python so the two stay comparable: if the browser ever
 * behaves differently from the offline scorer, the difference has to be in a
 * named place rather than in how the pieces were assembled.
 */

import { ChromaExtractor } from './chroma.js';
import { OnsetDetector } from './onset.js';
import { Verifier } from './verify.js';
import { makeScratch, rms, spectrum } from './dsp.js';
import { frameIntervalMs } from './params.js';

export class Analyser {
  constructor(params, target = null) {
    this.params = params;
    this.extractor = new ChromaExtractor(params);
    this.onset = new OnsetDetector(params);
    this.verifier = target ? new Verifier(target, params) : null;
    this.scratch = makeScratch(params.fftSize);
    this.frameIndex = 0;
  }

  /**
   * Point the verifier at a new step.
   *
   * The onset detector is deliberately NOT reset. Its flux history describes
   * the audio stream, not the step; clearing it on every advance would return
   * the detector to its warm-up state at each step of a song, which is where
   * room noise reads as a strike.
   */
  setTarget(target) {
    this.verifier = target && target.length ? new Verifier(target, this.params) : null;
    this.frameIndex = 0;
  }

  /** Analyse one un-windowed frame of `params.fftSize` samples. */
  push(frame) {
    const spec = spectrum(frame, this.params.fftSize, this.scratch);
    const { chroma, notes } = this.extractor.compute(spec);
    const isOnset = this.onset.step(spec);
    const level = rms(frame);

    const verdict = this.verifier
      ? this.verifier.step(chroma, notes, isOnset, level)
      : null;

    const result = {
      index: this.frameIndex,
      timeMs: this.frameIndex * frameIntervalMs(this.params),
      rms: level,
      chroma,
      notes,
      flux: this.onset.lastFlux,
      fluxThreshold: this.onset.lastThreshold,
      onset: isOnset,
      verdict,
    };
    this.frameIndex++;
    return result;
  }
}

/**
 * Maintains the sliding analysis window and calls `onFrame` once per hop.
 *
 * The window is fftSize wide and advances by hop, exactly as
 * `dsp.iter_frames` does offline — so a given stretch of audio produces the
 * same frames live as it would in the scorer.
 */
export class SlidingWindow {
  constructor(params, onFrame) {
    this.params = params;
    this.onFrame = onFrame;
    this.window = new Float64Array(params.fftSize);
    this.primed = 0;
  }

  push(block) {
    const n = block.length;
    const w = this.window;
    // Shift left by n, append the new block.
    w.copyWithin(0, n);
    w.set(block, w.length - n);

    // Do not analyse until the window has been filled once. A window still
    // half full of startup zeros produces a spectrum that is real but does not
    // describe any audio that was actually played.
    this.primed = Math.min(this.primed + n, w.length);
    if (this.primed < w.length) return null;

    return this.onFrame(w);
  }

  reset() {
    this.window.fill(0);
    this.primed = 0;
  }
}
