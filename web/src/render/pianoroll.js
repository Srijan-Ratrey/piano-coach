/**
 * The piano-roll: note bars falling onto a keyboard along the bottom edge.
 *
 * The renderer does not own time. `setScroll(songTime)` is fed the session's
 * clock every frame and the view tracks it exactly — no easing here.
 *
 * That is deliberate. This file used to ease scroll toward the current step
 * with a fixed time constant, which made every transition take about the same
 * time no matter how far apart the notes were, so the motion carried no
 * rhythm. Pacing is a property of the music, so it lives with the step logic in
 * `session/session.js`; forward motion arrives here already rate-limited. A
 * backward jump (Back, Restart, loop wrap) snaps, which is what a deliberate
 * navigation should do.
 *
 * A bar's BOTTOM edge is its start time, and it extends upward by its
 * duration, so the moment a bar's bottom touches the line is the moment that
 * note is due.
 */

import { KeyboardGeometry, isBlackKey } from './keyboard.js';
import { RIGHT } from '../song/midi.js';

export const THEME = {
  background: '#0d1117',
  gridLine: '#181f28',
  gridLineOctave: '#2a3441',
  hitLine: '#e8eef7',
  hitGlow: 'rgba(232, 238, 247, 0.12)',
  right: '#ff9f43',
  rightDim: 'rgba(255, 159, 67, 0.30)',
  left: '#4aa3ff',
  leftDim: 'rgba(74, 163, 255, 0.30)',
  done: 'rgba(120, 132, 148, 0.30)',
  missed: 'rgba(240, 101, 90, 0.26)',
  missedKey: '#5d2b28',
  whiteKey: '#e9edf3',
  blackKey: '#171c24',
  keyEdge: '#0d1117',
  keyLabel: '#7d8794',
  text: '#e8eef7',
  textDim: '#7d8794',
};

const KEYBOARD_HEIGHT_RATIO = 0.17;
const KEYBOARD_MIN_HEIGHT = 78;
const KEYBOARD_MAX_HEIGHT = 150;
const PX_PER_SECOND = 165;
const MIN_BAR_LENGTH = 14;
const RANGE_PADDING = 2;
const MIN_RANGE_SEMITONES = 36;

export class PianoRoll {
  constructor(canvas, song) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.song = song;

    // Song-seconds, fed from the session clock each frame.
    this.scroll = song.steps.length ? song.steps[0].time : 0;
    this.currentStepIndex = 0;
    this.matchedKeys = new Map();
    this.missedKeys = new Map();
    this.missedSteps = new Set();
    this.flash = 0;
    this.missFlash = 0;

    this._resize();
  }

  _resize() {
    const dpr = window.devicePixelRatio || 1;
    const rect = this.canvas.getBoundingClientRect();
    const w = Math.max(320, Math.floor(rect.width));
    const h = Math.max(240, Math.floor(rect.height));

    this.canvas.width = Math.floor(w * dpr);
    this.canvas.height = Math.floor(h * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.width = w;
    this.height = h;

    const kbHeight = Math.round(
      Math.min(KEYBOARD_MAX_HEIGHT, Math.max(KEYBOARD_MIN_HEIGHT, h * KEYBOARD_HEIGHT_RATIO)),
    );
    this.hitY = h - kbHeight;

    const { min, max } = pitchRange(this.song);
    this.keyboard = new KeyboardGeometry(min, max, w, this.hitY, kbHeight);
  }

  resize() {
    this._resize();
  }

  setStep(index) {
    this.currentStepIndex = index;
  }

  /** Track the session clock. Song-seconds. */
  setScroll(songTime) {
    this.scroll = songTime;
  }

  /** Called when a step is confirmed, to flash the matched keys. */
  markMatched(midiNotes) {
    this.flash = 1;
    this.matchedKeys = new Map(midiNotes.map((m) => [m, THEME.hitLine]));
  }

  /**
   * Play mode: a step that went by unplayed.
   *
   * The bar tint alone is nearly useless as feedback — a step is only declared
   * missed a grace period AFTER it crosses the line, by which point the bar is
   * mostly clipped under the keyboard. So the miss is flashed on the *keys*,
   * which are stationary and where the player's eyes already are.
   */
  markMissed(index, midiNotes = []) {
    this.missedSteps.add(index);
    this.missFlash = 1;
    this.missedKeys = new Map(midiNotes.map((m) => [m, THEME.missedKey]));
  }

  clearMissed() {
    this.missedSteps.clear();
    this.missedKeys = new Map();
    this.missFlash = 0;
  }

  tick(dt) {
    this.flash = Math.max(0, this.flash - dt * 3);
    if (this.flash === 0) this.matchedKeys = new Map();

    this.missFlash = Math.max(0, (this.missFlash ?? 0) - dt * 1.6);
    if (this.missFlash === 0 && this.missedKeys?.size) this.missedKeys = new Map();
  }

  draw() {
    const ctx = this.ctx;
    const { width, height, hitY } = this;
    const kb = this.keyboard;

    ctx.fillStyle = THEME.background;
    ctx.fillRect(0, 0, width, height);

    this._drawLanes(ctx, hitY);

    const currentStep = this.song.steps[this.currentStepIndex];
    const lookAhead = hitY / PX_PER_SECOND;
    const visibleFrom = this.scroll - 0.8;
    const visibleTo = this.scroll + lookAhead + 0.5;

    for (const step of this.song.steps) {
      if (step.endTime < visibleFrom || step.time > visibleTo) continue;
      const isCurrent = step.index === this.currentStepIndex;
      const isPast = step.index < this.currentStepIndex;
      const isMissed = this.missedSteps.has(step.index);
      for (const note of step.notes) {
        this._drawNote(ctx, note, hitY, isCurrent, isPast, isMissed);
      }
    }

    this._drawHitLine(ctx, hitY, width);

    const highlighted = new Map([...this.missedKeys, ...this.matchedKeys]);
    if (currentStep) {
      for (const note of currentStep.notes) {
        if (!highlighted.has(note.midi)) {
          // Target keys are highlighted as a guide only — DECISIONS #9. They
          // are never an input surface.
          highlighted.set(note.midi, note.hand === RIGHT ? THEME.rightDim : THEME.leftDim);
        }
      }
    }
    kb.draw(ctx, { highlighted, theme: THEME });
  }

  _drawLanes(ctx, hitY) {
    const kb = this.keyboard;
    for (const m of kb.whites) {
      const x = Math.round(kb.centreX(m) + kb.whiteWidth / 2);
      ctx.fillStyle = (m + 1) % 12 === 0 ? THEME.gridLineOctave : THEME.gridLine;
      ctx.fillRect(x, 0, 1, hitY);
    }
  }

  _drawNote(ctx, note, hitY, isCurrent, isPast, isMissed = false) {
    const kb = this.keyboard;
    if (!kb.contains(note.midi)) return;

    const laneW = kb.laneWidth(note.midi);
    const barW = Math.max(4, laneW - 3);
    const x = kb.centreX(note.midi) - barW / 2;

    // Bottom edge = the note's start; the bar extends upward by its duration.
    const bottom = hitY - (note.time - this.scroll) * PX_PER_SECOND;
    const length = Math.max(MIN_BAR_LENGTH, note.duration * PX_PER_SECOND - 3);
    const top = bottom - length;

    if (bottom < 0 || top > hitY) return;

    let colour;
    if (isMissed) colour = THEME.missed;
    else if (isPast) colour = THEME.done;
    else if (note.hand === RIGHT) colour = isCurrent ? THEME.right : THEME.rightDim;
    else colour = isCurrent ? THEME.left : THEME.leftDim;

    // Clip above the line so bars slide under the keyboard rather than over it.
    ctx.save();
    ctx.beginPath();
    ctx.rect(0, 0, this.width, hitY);
    ctx.clip();

    roundRect(ctx, x, top, barW, length, Math.min(5, barW / 2));
    ctx.fillStyle = colour;
    ctx.fill();

    if (isCurrent) {
      ctx.strokeStyle = THEME.hitLine;
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }

    // Finger numbers — display only, never verified (DECISIONS, hard limits).
    // Placed at the bar's leading (bottom) edge, which is the part the player
    // is actually looking at as it arrives.
    if (note.finger && barW >= 15 && length >= 20) {
      ctx.fillStyle = isPast ? THEME.textDim : '#12161c';
      ctx.font = `600 ${Math.min(13, barW - 4)}px ui-sans-serif, system-ui, sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'bottom';
      ctx.fillText(String(note.finger), x + barW / 2, Math.min(hitY - 3, bottom - 5));
    }

    ctx.restore();
  }

  _drawHitLine(ctx, hitY, width) {
    if (this.flash > 0) {
      ctx.fillStyle = `rgba(232, 238, 247, ${0.14 * this.flash})`;
      ctx.fillRect(0, hitY - 40, width, 40);
    }
    const grad = ctx.createLinearGradient(0, hitY - 30, 0, hitY);
    grad.addColorStop(0, 'rgba(0,0,0,0)');
    grad.addColorStop(1, THEME.hitGlow);
    ctx.fillStyle = grad;
    ctx.fillRect(0, hitY - 30, width, 30);

    ctx.fillStyle = THEME.hitLine;
    ctx.fillRect(0, hitY - 2, width, 2);
  }
}

function pitchRange(song) {
  let min = Infinity;
  let max = -Infinity;
  for (const n of song.notes) {
    if (n.midi < min) min = n.midi;
    if (n.midi > max) max = n.midi;
  }
  if (!Number.isFinite(min)) return { min: 48, max: 84 };

  min -= RANGE_PADDING;
  max += RANGE_PADDING;
  // Keep the keys from becoming absurdly wide on a one-octave song.
  while (max - min < MIN_RANGE_SEMITONES) {
    max++;
    if (max - min < MIN_RANGE_SEMITONES) min--;
  }
  return { min: Math.max(21, min), max: Math.min(108, max) };
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}
