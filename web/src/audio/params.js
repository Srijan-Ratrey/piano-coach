/**
 * Verification constants — the JS side of spike/params.py.
 *
 * These values are only meaningful alongside the exact transform that produced
 * them, which is why `test/golden.test.js` exists. Do not edit a threshold here
 * without re-running the Python sweep; the two must stay in step or the spike's
 * measurement stops describing this code.
 *
 * See spike/ALGORITHM.md for what each one does and why.
 */

export const DEFAULT_PARAMS = Object.freeze({
  sampleRate: 48000,
  fftSize: 8192,
  hop: 2048,

  midiLow: 36, // C2
  midiHigh: 96, // C7

  harmonicWeights: Object.freeze([1.0, 0.5, 0.25, 0.125]),
  neighbourhoodCents: 25.0,
  maxNeighbourBins: 3,

  presentThresh: 0.35,
  extraMargin: 0.5,
  stabilityMs: 250,

  onsetFluxRatio: 2.0,
  onsetWindowFrames: 21,
  onsetFluxFloor: 0.1,
  onsetRefractoryMs: 170,

  silenceRms: 0.005,

  octaveMode: false,
  harmonicExclusion: false,
});

export const MIN_HISTORY_FRAMES = 3;

/** Milliseconds between successive analysis frames. */
export function frameIntervalMs(p) {
  return (1000 * p.hop) / p.sampleRate;
}

export function stabilityFrames(p) {
  return Math.max(1, Math.ceil(p.stabilityMs / frameIntervalMs(p)));
}

/**
 * Frames a single instantaneous strike occupies as the window slides over it.
 * A property of the transform, not a tuning choice.
 */
export function windowSmearFrames(p) {
  return Math.floor(p.fftSize / p.hop) + 1;
}

export function onsetRefractoryFrames(p) {
  const requested = Math.round(p.onsetRefractoryMs / frameIntervalMs(p));
  return Math.max(1, windowSmearFrames(p), requested);
}

export function binHz(p) {
  return p.sampleRate / p.fftSize;
}

/**
 * Copy with overrides.
 *
 * `sampleRate` in particular is expected to be overridden at runtime:
 * AudioContext is usually 48 kHz on macOS but is not guaranteed, and the band
 * table must be built from the rate actually in use.
 */
export function withParams(p, overrides) {
  return Object.freeze({ ...p, ...overrides });
}
