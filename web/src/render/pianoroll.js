/**
 * The piano-roll: note bars scrolling horizontally toward a vertical keyboard.
 *
 * In wait-mode there is no clock. The scroll position IS the current step's
 * time, so the view freezes on the step being waited for and glides forward
 * when it is confirmed. That is the whole visual grammar of the mode — nothing
 * moves until the player plays.
 */

import { KeyboardGeometry, isBlackKey } from './keyboard.js';
import { RIGHT } from '../song/midi.js';

export const THEME = {
  background: '#0d1117',
  gridLine: '#1b222c',
  gridLineOctave: '#2a3441',
  hitLine: '#e8eef7',
  hitGlow: 'rgba(232, 238, 247, 0.14)',
  right: '#ff9f43',
  rightDim: 'rgba(255, 159, 67, 0.28)',
  left: '#4aa3ff',
  leftDim: 'rgba(74, 163, 255, 0.28)',
  done: 'rgba(120, 132, 148, 0.32)',
  whiteKey: '#e9edf3',
  blackKey: '#171c24',
  keyEdge: '#0d1117',
  keyLabel: '#9aa4b2',
  text: '#e8eef7',
  textDim: '#7d8794',
};

const KEYBOARD_WIDTH = 96;
const PX_PER_SECOND = 190;
const MIN_BAR_LENGTH = 16;
const RANGE_PADDING = 3;
const MIN_RANGE_SEMITONES = 25;

export class PianoRoll {
  constructor(canvas, song) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.song = song;

    // Scroll is measured in song-seconds and tweened toward the current step,
    // rather than snapping, so an advance reads as motion instead of a jump.
    this.scroll = song.steps.length ? song.steps[0].time : 0;
    this.scrollTarget = this.scroll;
    this.currentStepIndex = 0;
    this.matchedKeys = new Map();
    this.flash = 0;

    this._resize();
  }

  _resize() {
    const dpr = window.devicePixelRatio || 1;
    const rect = this.canvas.getBoundingClientRect();
    const w = Math.max(320, Math.floor(rect.width));
    const h = Math.max(200, Math.floor(rect.height));

    this.canvas.width = Math.floor(w * dpr);
    this.canvas.height = Math.floor(h * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.width = w;
    this.height = h;

    const { min, max } = pitchRange(this.song);
    this.keyboard = new KeyboardGeometry(min, max, 0, h, KEYBOARD_WIDTH);
  }

  resize() {
    this._resize();
  }

  setStep(index) {
    this.currentStepIndex = index;
    const step = this.song.steps[index];
    if (step) this.scrollTarget = step.time;
  }

  /** Called when a step is confirmed, to flash the matched keys. */
  markMatched(midiNotes) {
    this.flash = 1;
    this.matchedKeys = new Map(midiNotes.map((m) => [m, THEME.hitLine]));
  }

  tick(dt) {
    // Critically damped-ish easing; fast enough not to feel laggy, slow enough
    // that the eye can follow which bar just left.
    const k = 1 - Math.exp(-dt * 11);
    this.scroll += (this.scrollTarget - this.scroll) * k;
    this.flash = Math.max(0, this.flash - dt * 3);
    if (this.flash === 0) this.matchedKeys = new Map();
  }

  draw() {
    const ctx = this.ctx;
    const { width, height } = this;
    const kb = this.keyboard;
    const hitX = KEYBOARD_WIDTH;

    ctx.fillStyle = THEME.background;
    ctx.fillRect(0, 0, width, height);

    this._drawLanes(ctx, hitX, width);

    const currentStep = this.song.steps[this.currentStepIndex];
    const visibleFrom = this.scroll - 1.2;
    const visibleTo = this.scroll + (width - hitX) / PX_PER_SECOND + 0.5;

    for (const step of this.song.steps) {
      if (step.endTime < visibleFrom || step.time > visibleTo) continue;
      const isCurrent = step.index === this.currentStepIndex;
      const isPast = step.index < this.currentStepIndex;
      for (const note of step.notes) {
        this._drawNote(ctx, note, step, hitX, isCurrent, isPast);
      }
    }

    this._drawHitLine(ctx, hitX, height);

    const highlighted = new Map(this.matchedKeys);
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

  _drawLanes(ctx, hitX, width) {
    const kb = this.keyboard;
    for (let m = kb.midiLow; m <= kb.midiHigh; m++) {
      if (isBlackKey(m)) continue;
      const y = kb.centreY(m) + kb.whiteHeight / 2;
      ctx.fillStyle = m % 12 === 0 ? THEME.gridLineOctave : THEME.gridLine;
      ctx.fillRect(hitX, Math.round(y), width - hitX, 1);
    }
  }

  _drawNote(ctx, note, step, hitX, isCurrent, isPast) {
    const kb = this.keyboard;
    if (!kb.contains(note.midi)) return;

    const x = hitX + (note.time - this.scroll) * PX_PER_SECOND;
    const length = Math.max(MIN_BAR_LENGTH, note.duration * PX_PER_SECOND - 2);
    const laneH = kb.laneHeight(note.midi);
    const barH = Math.max(4, laneH - 2);
    const y = kb.centreY(note.midi) - barH / 2;

    if (x + length < hitX || x > this.width) return;

    let colour;
    if (isPast) colour = THEME.done;
    else if (note.hand === RIGHT) colour = isCurrent ? THEME.right : THEME.rightDim;
    else colour = isCurrent ? THEME.left : THEME.leftDim;

    // Clip to the right of the hit line so bars slide under the keyboard
    // rather than over it.
    ctx.save();
    ctx.beginPath();
    ctx.rect(hitX, 0, this.width - hitX, this.height);
    ctx.clip();

    roundRect(ctx, x, y, length, barH, Math.min(5, barH / 2));
    ctx.fillStyle = colour;
    ctx.fill();

    if (isCurrent) {
      ctx.strokeStyle = THEME.hitLine;
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }

    // Finger numbers — display only, never verified (DECISIONS, hard limits).
    if (note.finger && barH >= 13 && length >= 20) {
      ctx.fillStyle = isPast ? THEME.textDim : '#12161c';
      ctx.font = `600 ${Math.min(12, barH - 3)}px ui-sans-serif, system-ui, sans-serif`;
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      ctx.fillText(String(note.finger), Math.max(hitX + 4, x + 5), y + barH / 2 + 0.5);
    }

    ctx.restore();
  }

  _drawHitLine(ctx, hitX, height) {
    if (this.flash > 0) {
      ctx.fillStyle = `rgba(232, 238, 247, ${0.16 * this.flash})`;
      ctx.fillRect(hitX, 0, 46, height);
    }
    const grad = ctx.createLinearGradient(hitX, 0, hitX + 34, 0);
    grad.addColorStop(0, THEME.hitGlow);
    grad.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = grad;
    ctx.fillRect(hitX, 0, 34, height);

    ctx.fillStyle = THEME.hitLine;
    ctx.fillRect(hitX, 0, 2, height);
  }
}

function pitchRange(song) {
  let min = Infinity;
  let max = -Infinity;
  for (const n of song.notes) {
    if (n.midi < min) min = n.midi;
    if (n.midi > max) max = n.midi;
  }
  if (!Number.isFinite(min)) return { min: 55, max: 84 };

  min -= RANGE_PADDING;
  max += RANGE_PADDING;
  // Keep the keys from becoming absurdly tall on a one-octave song.
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
