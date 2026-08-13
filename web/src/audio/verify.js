/**
 * The verdict state machine — the JS side of spike/core/verify.py.
 * See spike/ALGORITHM.md §4.
 *
 * This is NOT a chord recogniser. In wait-mode the app already knows the next
 * notes, so it answers one yes/no question about a known target (DECISION #2).
 * That single reframing is what makes microphone polyphony tractable at all.
 *
 * Four conditions must hold together. Dropping any one breaks it in a specific,
 * known way:
 *   1. every target pitch present   — else nothing detects
 *   2. no unexpected pitch loud     — else overtones make everything "correct"
 *   3. an onset was seen            — else a ringing chord advances the song
 *   4. held for stabilityMs         — else a brushed key counts as a note
 */

import { stabilityFrames } from './params.js';

/**
 * Pitch-class offsets of harmonics 2..n, in semitones mod 12.
 * Harmonic h sits 12*log2(h) semitones up: 2nd = octave (0), 3rd = fifth (+7),
 * 4th = two octaves (0), 5th = major third (+4).
 */
export function harmonicPitchClassOffsets(nHarmonics) {
  const out = new Set();
  for (let h = 2; h <= nHarmonics; h++) {
    out.add(((Math.round(12 * Math.log2(h)) % 12) + 12) % 12);
  }
  return [...out].sort((a, b) => a - b);
}

export class Verifier {
  /**
   * @param {number[]} target pitch classes 0-11, or MIDI notes in octave mode
   */
  constructor(target, params) {
    this.params = params;
    this.target = [...new Set(target)].sort((a, b) => a - b);
    this.stabilityFrames = stabilityFrames(params);

    if (params.octaveMode) {
      for (const m of this.target) {
        if (m < params.midiLow || m > params.midiHigh) {
          throw new Error(`target MIDI ${m} outside [${params.midiLow}, ${params.midiHigh}]`);
        }
      }
    } else {
      for (const pc of this.target) {
        if (pc < 0 || pc > 11) throw new Error(`target pitch class ${pc} outside 0-11`);
      }
    }

    this._exempt = this._computeExempt();
    this.reset();
  }

  _computeExempt() {
    if (!this.params.harmonicExclusion) return new Set();
    const n = this.params.harmonicWeights.length;
    if (this.params.octaveMode) {
      const out = new Set();
      for (const m of this.target) {
        for (let h = 2; h <= n; h++) out.add(m + Math.round(12 * Math.log2(h)));
      }
      return out;
    }
    const offsets = harmonicPitchClassOffsets(n);
    const out = new Set();
    for (const pc of this.target) for (const off of offsets) out.add((pc + off) % 12);
    return out;
  }

  reset() {
    this._frame = 0;
    this._armed = false;
    this._held = 0;
    this._latched = false;
    this.onsetFrame = null;
    this.confirmFrame = null;
  }

  /**
   * Advance one frame.
   * @returns {{frame:number, silent:boolean, onset:boolean, armed:boolean,
   *   targetsPresent:boolean, extras:number[], framesHeld:number,
   *   confirmedNow:boolean, latched:boolean}}
   */
  step(chroma, notes, isOnset, frameRms) {
    const p = this.params;
    const frame = this._frame++;

    if (frameRms < p.silenceRms) {
      // Silence is not evidence. Drop the held count but PRESERVE `armed`:
      // clearing it would let the next chord confirm with no strike of its own
      // after any quiet gap — the exact failure onset gating exists to prevent.
      this._held = 0;
      return {
        frame, silent: true, onset: false, armed: this._armed,
        targetsPresent: false, extras: [], framesHeld: 0,
        confirmedNow: false, latched: this._latched,
      };
    }

    if (isOnset) {
      if (!this._armed) {
        this._armed = true;
        this.onsetFrame = frame;
      } else {
        // A second strike restarts the clock: whatever was held before belongs
        // to the previous attack.
        this._held = 0;
      }
    }

    const vector = p.octaveMode ? notes : chroma;
    const offset = p.octaveMode ? p.midiLow : 0;

    let peak = 0;
    for (let i = 0; i < vector.length; i++) if (vector[i] > peak) peak = vector[i];

    if (peak <= 0) {
      this._held = 0;
      return {
        frame, silent: false, onset: isOnset, armed: this._armed,
        targetsPresent: false, extras: [], framesHeld: 0,
        confirmedNow: false, latched: this._latched,
      };
    }

    // Thresholds are fractions of the frame's own peak: loudness-independent
    // AND chord-size-independent. An absolute threshold would silently get
    // stricter as chords get denser, because a four-note chord spreads
    // normalised chroma thinner than a single note.
    const presentLevel = p.presentThresh * peak;
    const extraLevel = p.extraMargin * peak;

    const targetIdx = this.target.map((t) => t - offset);
    const targetSet = new Set(targetIdx);

    let targetsPresent = true;
    for (const i of targetIdx) {
      if (i < 0 || i >= vector.length || vector[i] < presentLevel) {
        targetsPresent = false;
        break;
      }
    }

    const extras = [];
    for (let i = 0; i < vector.length; i++) {
      if (vector[i] > extraLevel && !targetSet.has(i) && !this._exempt.has(i + offset)) {
        extras.push(i + offset);
      }
    }

    const ok = this._armed && targetsPresent && extras.length === 0;
    this._held = ok ? this._held + 1 : 0;

    let confirmedNow = false;
    if (!this._latched && this._held >= this.stabilityFrames) {
      this._latched = true;
      this.confirmFrame = frame;
      confirmedNow = true;
    }

    return {
      frame, silent: false, onset: isOnset, armed: this._armed,
      targetsPresent, extras, framesHeld: this._held,
      confirmedNow, latched: this._latched,
    };
  }
}
