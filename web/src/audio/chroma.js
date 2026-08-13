/**
 * Spectrum -> per-note energies -> 12-bin chroma.
 * The JS side of spike/core/chroma.py. See spike/ALGORITHM.md §2.
 *
 * The band table is built once at construction from the *actual* sample rate,
 * which matters in the browser: AudioContext is usually 48 kHz on macOS but is
 * not guaranteed, and a table built for the wrong rate mistunes every note.
 */

import { binHz } from './params.js';

export const A4_MIDI = 69;
export const A4_HZ = 440.0;

export const PITCH_CLASS_NAMES = Object.freeze([
  'C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B',
]);

export function midiToHz(midi) {
  return A4_HZ * Math.pow(2, (midi - A4_MIDI) / 12);
}

/** 60 -> "C4". Middle C is C4, matching the reference marker in DECISIONS. */
export function noteName(midi) {
  return `${PITCH_CLASS_NAMES[((midi % 12) + 12) % 12]}${Math.floor(midi / 12) - 1}`;
}

export class ChromaExtractor {
  constructor(params) {
    this.params = params;
    this.midiLow = params.midiLow;
    this.midiHigh = params.midiHigh;
    this.nNotes = params.midiHigh - params.midiLow + 1;

    const nBins = params.fftSize / 2 + 1;
    const nyquistBin = nBins - 1;
    const bw = binHz(params);
    const cents = params.neighbourhoodCents;
    const cap = params.maxNeighbourBins;

    // Flat band list: for each (note, harmonic) inside the analysed band, the
    // inclusive-exclusive bin range to peak-pick and the weight to apply.
    // Flat rather than nested so the per-frame loop is a single pass.
    const noteIdx = [];
    const weights = [];
    const lo = [];
    const hi = [];

    for (let h = 1; h <= params.harmonicWeights.length; h++) {
      const weight = params.harmonicWeights[h - 1];
      for (let i = 0; i < this.nNotes; i++) {
        const f = midiToHz(params.midiLow + i) * h;
        const centre = Math.round(f / bw);
        if (centre < 1 || centre > nyquistBin) continue;

        // Round edges to NEAREST, not outward. floor/ceil always widens the
        // band to two or three bins, re-creating the neighbour overlap the
        // cents window exists to remove.
        let l = Math.round((f * Math.pow(2, -cents / 1200)) / bw);
        let g = Math.round((f * Math.pow(2, cents / 1200)) / bw);

        l = Math.max(l, centre - cap);
        g = Math.min(g, centre + cap);
        l = Math.min(l, centre);
        g = Math.max(g, centre);

        l = Math.max(1, l);
        g = Math.min(nyquistBin, g);
        if (g < l) continue;

        noteIdx.push(i);
        weights.push(weight);
        lo.push(l);
        hi.push(g + 1);
      }
    }

    this.bandNote = Int32Array.from(noteIdx);
    this.bandWeight = Float64Array.from(weights);
    this.bandLo = Int32Array.from(lo);
    this.bandHi = Int32Array.from(hi);

    this.pitchClasses = new Int32Array(this.nNotes);
    for (let i = 0; i < this.nNotes; i++) {
      this.pitchClasses[i] = (params.midiLow + i) % 12;
    }

    this._notes = new Float64Array(this.nNotes);
    this._chroma = new Float64Array(12);
  }

  noteIndex(midi) {
    if (midi < this.midiLow || midi > this.midiHigh) {
      throw new Error(`MIDI ${midi} outside analysed range [${this.midiLow}, ${this.midiHigh}]`);
    }
    return midi - this.midiLow;
  }

  /**
   * Returns `{ chroma, notes }`, both L1-normalised — which is what makes
   * everything downstream loudness-independent, so one threshold serves every
   * dynamic with no gain stage.
   *
   * The returned arrays are reused between calls. Copy them if you need to keep
   * one past the next `compute()`.
   */
  compute(spec) {
    const notes = this._notes;
    const chroma = this._chroma;
    notes.fill(0);
    chroma.fill(0);

    // Peak, not sum, within each band: a partial occupies one or two bins and
    // the rest of the band is noise, so summing would reward wide bands for
    // containing more noise.
    for (let b = 0; b < this.bandNote.length; b++) {
      const lo = this.bandLo[b];
      const hi = this.bandHi[b];
      let peak = spec[lo];
      for (let k = lo + 1; k < hi; k++) if (spec[k] > peak) peak = spec[k];
      notes[this.bandNote[b]] += this.bandWeight[b] * peak;
    }

    let noteSum = 0;
    for (let i = 0; i < this.nNotes; i++) {
      chroma[this.pitchClasses[i]] += notes[i];
      noteSum += notes[i];
    }

    let chromaSum = 0;
    for (let p = 0; p < 12; p++) chromaSum += chroma[p];

    if (chromaSum > 0) for (let p = 0; p < 12; p++) chroma[p] /= chromaSum;
    if (noteSum > 0) for (let i = 0; i < this.nNotes; i++) notes[i] /= noteSum;

    return { chroma, notes };
  }
}
