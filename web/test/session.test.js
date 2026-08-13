/**
 * End-to-end: synthetic audio -> wait-mode loop -> steps advance.
 *
 * The golden test proves the DSP matches Python. This proves the *wiring*:
 * MIDI parsing, step grouping, target extraction, the sliding window, the
 * analyser and the session state machine all agree with each other. Those are
 * separate failure modes — a correct verifier fed the wrong target is still a
 * broken app.
 *
 * Runs headless, so a regression here shows up without opening a browser.
 */

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import { DEFAULT_PARAMS } from '../src/audio/params.js';
import { midiToHz } from '../src/audio/chroma.js';
import { filterByHand, groupIntoSteps, parseMidi, targetForStep } from '../src/song/midi.js';
import { WaitModeSession } from '../src/session/waitmode.js';

const here = dirname(fileURLToPath(import.meta.url));
const midiDir = join(here, '..', 'public', 'midi');

const SR = DEFAULT_PARAMS.sampleRate;
const PARTIALS = [1.0, 0.5, 0.28, 0.16, 0.09, 0.05];

/** A struck, decaying harmonic tone — crude, but harmonically structured. */
function pianoNote(midi, seconds, amp = 0.25) {
  const n = Math.floor(seconds * SR);
  const out = new Float64Array(n);
  const f0 = midiToHz(midi);
  for (let h = 1; h <= PARTIALS.length; h++) {
    const f = f0 * h;
    if (f >= SR / 2) break;
    const w = PARTIALS[h - 1];
    const decay = 1.4 * Math.sqrt(h);
    for (let i = 0; i < n; i++) {
      const t = i / SR;
      out[i] += w * Math.exp(-decay * t) * Math.sin(2 * Math.PI * f * t + 0.7 * h);
    }
  }
  let peak = 0;
  for (let i = 0; i < n; i++) peak = Math.max(peak, Math.abs(out[i]));
  if (peak > 0) for (let i = 0; i < n; i++) out[i] = (out[i] / peak) * amp;
  // A soft attack ramp, so the first sample is not a step discontinuity.
  const ramp = Math.floor(0.006 * SR);
  for (let i = 0; i < ramp && i < n; i++) out[i] *= i / ramp;
  return out;
}

/** Silence, then the notes struck together and left to ring. */
function renderStep(midiNotes, seconds = 1.6, leadSeconds = 0.35) {
  const lead = Math.floor(leadSeconds * SR);
  const total = lead + Math.floor(seconds * SR);
  const out = new Float64Array(total);
  for (const m of midiNotes) {
    const note = pianoNote(m, seconds);
    for (let i = 0; i < note.length; i++) out[lead + i] += note[i];
  }
  let peak = 0;
  for (let i = 0; i < total; i++) peak = Math.max(peak, Math.abs(out[i]));
  if (peak > 0.9) for (let i = 0; i < total; i++) out[i] *= 0.9 / peak;
  // Room tone, so the silence gate and adaptive onset threshold see something
  // other than mathematical zero — as they will in any real room.
  let seed = 12345;
  for (let i = 0; i < total; i++) {
    seed = (seed * 1103515245 + 12345) & 0x7fffffff;
    out[i] += ((seed / 0x7fffffff) - 0.5) * 0.0016;
  }
  return out;
}

function feed(session, samples) {
  const hop = DEFAULT_PARAMS.hop;
  for (let i = 0; i + hop <= samples.length; i += hop) {
    session.pushAudio(samples.subarray(i, i + hop));
  }
}

function loadSong(file, hand = 'right') {
  const buf = readFileSync(join(midiDir, file));
  const parsed = parseMidi(new Uint8Array(buf).buffer, file);
  return filterByHand(parsed, hand);
}

describe('MIDI parsing and step grouping', () => {
  test('bundled songs parse into steps with hands assigned', () => {
    for (const file of ['scale-and-chords.mid', 'ode-to-joy.mid', 'twinkle.mid', 'chord-drill.mid']) {
      const buf = readFileSync(join(midiDir, file));
      const song = parseMidi(new Uint8Array(buf).buffer, file);
      assert.ok(song.notes.length > 0, `${file} has notes`);
      assert.ok(song.steps.length > 0, `${file} has steps`);
      assert.ok(
        song.notes.some((n) => n.hand === 'right') && song.notes.some((n) => n.hand === 'left'),
        `${file} has both hands`,
      );
      for (const step of song.steps) {
        assert.ok(step.pitchClasses.length > 0, 'step has a target');
        assert.ok(step.notes.every((n) => n.finger >= 1 && n.finger <= 5), 'fingers assigned');
      }
    }
  });

  test('near-simultaneous notes become one step, spread ones do not', () => {
    const chord = groupIntoSteps([
      { midi: 60, time: 0.0, duration: 1, hand: 'right' },
      { midi: 64, time: 0.02, duration: 1, hand: 'right' },
      { midi: 67, time: 0.04, duration: 1, hand: 'right' },
    ]);
    assert.equal(chord.length, 1);
    assert.deepEqual(chord[0].pitchClasses, [0, 4, 7]);

    const melody = groupIntoSteps([
      { midi: 60, time: 0.0, duration: 1, hand: 'right' },
      { midi: 64, time: 0.5, duration: 1, hand: 'right' },
    ]);
    assert.equal(melody.length, 2);
  });

  test('a chord voiced across octaves collapses to one question per pitch class', () => {
    const [step] = groupIntoSteps([
      { midi: 48, time: 0, duration: 1, hand: 'left' },
      { midi: 60, time: 0, duration: 1, hand: 'right' },
      { midi: 64, time: 0, duration: 1, hand: 'right' },
    ]);
    assert.deepEqual(targetForStep(step, DEFAULT_PARAMS), [0, 4]);
  });
});

describe('wait-mode loop against synthetic playing', () => {
  test('playing the right notes advances the step', () => {
    const song = loadSong('scale-and-chords.mid');
    const session = new WaitModeSession(song, DEFAULT_PARAMS, {});
    session.start();

    const first = session.currentStep;
    assert.equal(session.stepIndex, 0);

    feed(session, renderStep(first.midiNotes));
    assert.equal(session.stepIndex, 1, 'should have advanced past the first step');
    assert.equal(session.confirmedCount, 1);
  });

  test('playing the WRONG notes does not advance', () => {
    const song = loadSong('scale-and-chords.mid');
    const session = new WaitModeSession(song, DEFAULT_PARAMS, {});
    session.start();

    // The song opens on C4; play F#4 + A#4, sharing no pitch class with it.
    feed(session, renderStep([66, 70]));
    assert.equal(session.stepIndex, 0, 'a wrong chord must not advance the song');
    assert.equal(session.confirmedCount, 0);
  });

  test('silence does not advance', () => {
    const song = loadSong('scale-and-chords.mid');
    const session = new WaitModeSession(song, DEFAULT_PARAMS, {});
    session.start();
    feed(session, new Float64Array(SR * 2));
    assert.equal(session.stepIndex, 0);
  });

  test('a run of correct steps plays through in order', () => {
    const song = loadSong('scale-and-chords.mid');
    const confirmed = [];
    const session = new WaitModeSession(song, DEFAULT_PARAMS, {
      onConfirm: (step) => confirmed.push(step.index),
    });
    session.start();

    for (let i = 0; i < 6; i++) {
      const step = session.currentStep;
      assert.ok(step, `step ${i} exists`);
      feed(session, renderStep(step.midiNotes));
    }

    assert.deepEqual(confirmed, [0, 1, 2, 3, 4, 5]);
    assert.equal(session.stepIndex, 6);
  });

  test('skip advances without counting as played', () => {
    const song = loadSong('twinkle.mid');
    const session = new WaitModeSession(song, DEFAULT_PARAMS, {});
    session.start();
    session.skip();
    assert.equal(session.stepIndex, 1);
    assert.equal(session.confirmedCount, 0, 'a skipped step is not a played one');
  });

  test('a loop range wraps back to its start', () => {
    const song = loadSong('twinkle.mid');
    const session = new WaitModeSession(song, DEFAULT_PARAMS, {});
    session.start();
    session.setLoop(0, 2);
    session.skip();
    session.skip();
    session.skip();
    assert.equal(session.stepIndex, 0, 'should wrap to the loop start');
  });

  test('finishing the song reports a summary', () => {
    const song = loadSong('twinkle.mid');
    let summary = null;
    const session = new WaitModeSession(song, DEFAULT_PARAMS, {
      onFinish: (s) => { summary = s; },
    });
    session.start();
    for (let i = 0; i < song.steps.length; i++) session.skip();
    assert.ok(summary, 'onFinish fired');
    assert.equal(summary.steps, song.steps.length);
  });
});
