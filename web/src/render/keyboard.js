/**
 * Geometry for a keyboard drawn VERTICALLY along one edge, pitch increasing
 * upward — the landscape layout PLAN specifies, with note bars scrolling
 * horizontally toward it.
 *
 * Lanes are key-aligned rather than one-per-semitone: a white note's bar is as
 * tall as its white key, a black note's bar is thinner and sits on the boundary
 * between the two whites it lives between. Uniform semitone lanes would be
 * simpler but would not line up with the keys they point at, which is the one
 * thing this view has to get right.
 */

const BLACK_PITCH_CLASSES = new Set([1, 3, 6, 8, 10]);

export function isBlackKey(midi) {
  return BLACK_PITCH_CLASSES.has(((midi % 12) + 12) % 12);
}

export class KeyboardGeometry {
  /**
   * @param {number} midiLow  lowest key shown (forced to a white key)
   * @param {number} midiHigh highest key shown (forced to a white key)
   */
  constructor(midiLow, midiHigh, top, height, width) {
    while (isBlackKey(midiLow) && midiLow > 0) midiLow--;
    while (isBlackKey(midiHigh) && midiHigh < 127) midiHigh++;

    this.midiLow = midiLow;
    this.midiHigh = midiHigh;
    this.top = top;
    this.height = height;
    this.width = width;

    this.whites = [];
    this.whiteIndex = new Map();
    for (let m = midiLow; m <= midiHigh; m++) {
      if (!isBlackKey(m)) {
        this.whiteIndex.set(m, this.whites.length);
        this.whites.push(m);
      }
    }

    this.whiteHeight = height / Math.max(1, this.whites.length);
    this.blackHeight = this.whiteHeight * 0.62;
    this.blackWidth = width * 0.62;
    this.bottom = top + height;
  }

  contains(midi) {
    return midi >= this.midiLow && midi <= this.midiHigh;
  }

  /** Vertical centre of a key, in pixels. */
  centreY(midi) {
    if (isBlackKey(midi)) {
      // A black key sits on the boundary between the white below it and the
      // next white up.
      const below = this.whiteIndex.get(midi - 1);
      if (below === undefined) return this.bottom;
      return this.bottom - (below + 1) * this.whiteHeight;
    }
    const i = this.whiteIndex.get(midi);
    if (i === undefined) return this.bottom;
    return this.bottom - (i + 0.5) * this.whiteHeight;
  }

  /** Lane height for a note bar at this pitch. */
  laneHeight(midi) {
    return isBlackKey(midi) ? this.blackHeight : this.whiteHeight;
  }

  /** Draw the keyboard strip at x = 0..width. */
  draw(ctx, { highlighted = new Map(), theme }) {
    const { width } = this;

    // White keys first, so black keys overlay them.
    for (const m of this.whites) {
      const y = this.centreY(m) - this.whiteHeight / 2;
      const colour = highlighted.get(m);
      ctx.fillStyle = colour ?? theme.whiteKey;
      ctx.fillRect(0, y, width, this.whiteHeight - 1);
      ctx.strokeStyle = theme.keyEdge;
      ctx.lineWidth = 1;
      ctx.strokeRect(0.5, y + 0.5, width - 1, this.whiteHeight - 2);
    }

    for (let m = this.midiLow; m <= this.midiHigh; m++) {
      if (!isBlackKey(m)) continue;
      const y = this.centreY(m) - this.blackHeight / 2;
      const colour = highlighted.get(m);
      ctx.fillStyle = colour ?? theme.blackKey;
      ctx.fillRect(0, y, this.blackWidth, this.blackHeight);
    }

    // C markers — DECISIONS calls for a reference note name at the keyboard.
    ctx.font = '10px ui-monospace, SFMono-Regular, Menlo, monospace';
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = theme.keyLabel;
    for (const m of this.whites) {
      if (m % 12 !== 0) continue;
      if (this.whiteHeight < 9) continue;
      ctx.fillText(`C${Math.floor(m / 12) - 1}`, width - 4, this.centreY(m));
    }
  }
}
