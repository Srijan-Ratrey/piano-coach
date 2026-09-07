/**
 * Watches the raw microphone signal and says when the *input* is the problem.
 *
 * The browser side of `spike/live.py --check`, and it exists because the app
 * has no other way to distinguish "your playing was not recognised" from "no
 * audio is arriving at all". Those look identical from the player's seat — the
 * roll just sits there — and every condition below has been mistaken for a
 * detection failure at some point.
 *
 * The specific trap this catches: a browser can grant microphone permission and
 * then deliver silence — wrong input device, hardware mute, or the OS
 * withholding audio. Nothing throws. Every downstream number stays perfectly
 * well-formed and completely meaningless.
 *
 * Thresholds mirror spike/params.py so the browser and the offline tooling
 * agree on what "too quiet" and "clipping" mean.
 */

/** Verdicts. `verdict` reports the most serious one that holds. */
export const SILENT = 'silent';
export const CLIPPING = 'clipping';
export const QUIET = 'quiet';
export const OK = 'ok';

/** Seconds of history behind a verdict. Long enough not to flicker in the gaps
 *  between notes, short enough to notice a cable pulled out. */
export const WINDOW_SECONDS = 2.5;

/** Below this peak the input is delivering nothing. Not exactly zero: a
 *  muted-but-live input still carries dither and thermal noise, so a
 *  test for literal zero would miss the very case this is for. */
export const SILENT_PEAK = 1e-4;

export class InputMonitor {
  constructor(params) {
    this.params = params;
    this.clipThreshold = params.clipThreshold ?? 0.98;
    this.silenceRms = params.silenceRms;
    this.maxBlocks = Math.max(
      1,
      Math.ceil((WINDOW_SECONDS * params.sampleRate) / params.hop),
    );
    this.reset();
  }

  reset() {
    this._blocks = [];
    this._totals = { peak: 0, sumSquares: 0, clipped: 0, n: 0 };
    this.blocksSeen = 0;
  }

  /** Feed one raw block, exactly as it arrives from the worklet. */
  push(block) {
    let peak = 0;
    let sumSquares = 0;
    let clipped = 0;
    for (let i = 0; i < block.length; i++) {
      const v = block[i];
      const a = v < 0 ? -v : v;
      if (a > peak) peak = a;
      sumSquares += v * v;
      if (a >= this.clipThreshold) clipped++;
    }

    this._blocks.push({ peak, sumSquares, clipped, n: block.length });
    this.blocksSeen++;
    if (this._blocks.length > this.maxBlocks) this._blocks.shift();

    // Recomputed over the retained window rather than accumulated: dropping an
    // old block cannot then leave stale energy behind, and a running
    // subtraction of floats would drift. ~115 blocks, so it is cheap.
    let p = 0;
    let ss = 0;
    let c = 0;
    let n = 0;
    for (const b of this._blocks) {
      if (b.peak > p) p = b.peak;
      ss += b.sumSquares;
      c += b.clipped;
      n += b.n;
    }
    this._totals = { peak: p, sumSquares: ss, clipped: c, n };
  }

  get peak() {
    return this._totals.peak;
  }

  get rms() {
    const { sumSquares, n } = this._totals;
    return n ? Math.sqrt(sumSquares / n) : 0;
  }

  get clippedFraction() {
    const { clipped, n } = this._totals;
    return n ? clipped / n : 0;
  }

  /** True once enough audio has arrived for a verdict to mean anything. */
  get ready() {
    return this._blocks.length >= Math.min(this.maxBlocks, 8);
  }

  get verdict() {
    if (!this.ready) return OK;
    if (this.peak <= SILENT_PEAK) return SILENT;
    // Clipping outranks quiet: distortion generates harmonics that read as
    // extra notes, so it actively blocks confirmation rather than merely
    // making it harder.
    if (this.clippedFraction > 0) return CLIPPING;
    // The same gate the verifier uses to discard a frame, so this warns
    // precisely when frames are being thrown away as silence.
    if (this.rms < this.silenceRms) return QUIET;
    return OK;
  }

  /** One sentence naming the fix, not just the fault. */
  get advice() {
    switch (this.verdict) {
      case SILENT:
        return 'No audio arriving — check the input device and that nothing is muted.';
      case CLIPPING:
        return 'Too loud — distortion looks like extra notes and will block matches.';
      case QUIET:
        return 'Too quiet to register — move the mic closer or turn the piano up.';
      default:
        return '';
    }
  }
}
