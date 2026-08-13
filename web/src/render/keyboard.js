/**
 * Geometry for a keyboard drawn HORIZONTALLY along the bottom edge, pitch
 * increasing to the right — the conventional falling-note layout, with bars
 * descending onto the keys.
 *
 * Lanes are key-aligned rather than one-per-semitone: a white note's bar is as
 * wide as its white key, a black note's bar is narrower and sits over the
 * boundary between the two whites it lives between. Uniform semitone lanes
 * would be simpler but would not line up with the keys they point at, which is
 * the one thing this view has to get right.
 */

const BLACK_PITCH_CLASSES = new Set([1, 3, 6, 8, 10]);

export function isBlackKey(midi) {
  return BLACK_PITCH_CLASSES.has(((midi % 12) + 12) % 12);
}

export class KeyboardGeometry {
  /**
   * @param {number} midiLow  lowest key shown (widened to a white key)
   * @param {number} midiHigh highest key shown (widened to a white key)
   * @param {number} width    full canvas width
   * @param {number} top      y of the top of the keys
   * @param {number} height   key height
   */
  constructor(midiLow, midiHigh, width, top, height) {
    while (isBlackKey(midiLow) && midiLow > 0) midiLow--;
    while (isBlackKey(midiHigh) && midiHigh < 127) midiHigh++;

    this.midiLow = midiLow;
    this.midiHigh = midiHigh;
    this.width = width;
    this.top = top;
    this.height = height;
    this.bottom = top + height;

    this.whites = [];
    this.whiteIndex = new Map();
    for (let m = midiLow; m <= midiHigh; m++) {
      if (!isBlackKey(m)) {
        this.whiteIndex.set(m, this.whites.length);
        this.whites.push(m);
      }
    }

    this.whiteWidth = width / Math.max(1, this.whites.length);
    this.blackWidth = this.whiteWidth * 0.62;
    this.blackHeight = height * 0.62;
  }

  contains(midi) {
    return midi >= this.midiLow && midi <= this.midiHigh;
  }

  /** Horizontal centre of a key, in pixels. */
  centreX(midi) {
    if (isBlackKey(midi)) {
      // A black key straddles the boundary between the white below it and the
      // next white up.
      const below = this.whiteIndex.get(midi - 1);
      if (below === undefined) return 0;
      return (below + 1) * this.whiteWidth;
    }
    const i = this.whiteIndex.get(midi);
    if (i === undefined) return 0;
    return (i + 0.5) * this.whiteWidth;
  }

  /** Lane width for a note bar at this pitch. */
  laneWidth(midi) {
    return isBlackKey(midi) ? this.blackWidth : this.whiteWidth;
  }

  /** Draw the keyboard strip across the bottom. */
  draw(ctx, { highlighted = new Map(), theme }) {
    const { top, height } = this;

    // White keys first, so black keys overlay them.
    for (const m of this.whites) {
      const x = this.centreX(m) - this.whiteWidth / 2;
      const colour = highlighted.get(m);
      ctx.fillStyle = colour ?? theme.whiteKey;
      ctx.fillRect(x, top, this.whiteWidth - 1, height);
      ctx.strokeStyle = theme.keyEdge;
      ctx.lineWidth = 1;
      ctx.strokeRect(x + 0.5, top + 0.5, this.whiteWidth - 2, height - 1);
    }

    for (let m = this.midiLow; m <= this.midiHigh; m++) {
      if (!isBlackKey(m)) continue;
      const x = this.centreX(m) - this.blackWidth / 2;
      const colour = highlighted.get(m);
      ctx.fillStyle = colour ?? theme.blackKey;
      ctx.fillRect(x, top, this.blackWidth, this.blackHeight);
    }

    // C markers — DECISIONS calls for a reference note name at the keyboard.
    if (this.whiteWidth >= 15) {
      ctx.font = '10px ui-monospace, SFMono-Regular, Menlo, monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'bottom';
      ctx.fillStyle = theme.keyLabel;
      for (const m of this.whites) {
        if (m % 12 !== 0) continue;
        ctx.fillText(`C${Math.floor(m / 12) - 1}`, this.centreX(m), this.bottom - 5);
      }
    }
  }
}
