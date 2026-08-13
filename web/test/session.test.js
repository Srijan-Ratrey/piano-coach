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
import {
  GRACE_SECONDS,
  LEAD_IN_SECONDS,
  PLAY,
  PracticeSession,
  WAIT,
} from '../src/session/session.js';

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
    const session = new PracticeSession(song, DEFAULT_PARAMS, {});
    session.start();

    const first = session.currentStep;
    assert.equal(session.stepIndex, 0);

    feed(session, renderStep(first.midiNotes));
    assert.equal(session.stepIndex, 1, 'should have advanced past the first step');
    assert.equal(session.confirmedCount, 1);
  });

  test('playing the WRONG notes does not advance', () => {
    const song = loadSong('scale-and-chords.mid');
    const session = new PracticeSession(song, DEFAULT_PARAMS, {});
    session.start();

    // The song opens on C4; play F#4 + A#4, sharing no pitch class with it.
    feed(session, renderStep([66, 70]));
    assert.equal(session.stepIndex, 0, 'a wrong chord must not advance the song');
    assert.equal(session.confirmedCount, 0);
  });

  test('silence does not advance', () => {
    const song = loadSong('scale-and-chords.mid');
    const session = new PracticeSession(song, DEFAULT_PARAMS, {});
    session.start();
    feed(session, new Float64Array(SR * 2));
    assert.equal(session.stepIndex, 0);
  });

  test('a run of correct steps plays through in order', () => {
    const song = loadSong('scale-and-chords.mid');
    const confirmed = [];
    const session = new PracticeSession(song, DEFAULT_PARAMS, {
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
    const session = new PracticeSession(song, DEFAULT_PARAMS, {});
    session.start();
    session.skip();
    assert.equal(session.stepIndex, 1);
    assert.equal(session.confirmedCount, 0, 'a skipped step is not a played one');
  });

  test('a loop range wraps back to its start', () => {
    const song = loadSong('twinkle.mid');
    const session = new PracticeSession(song, DEFAULT_PARAMS, {});
    session.start();
    session.setLoop(0, 2);
    session.skip();
    session.skip();
    session.skip();
    assert.equal(session.stepIndex, 0, 'should wrap to the loop start');
  });

  test('audio arriving while paused is ignored', () => {
    const song = loadSong('scale-and-chords.mid');
    const session = new PracticeSession(song, DEFAULT_PARAMS, {});
    session.start();
    session.togglePause();
    feed(session, renderStep(session.currentStep.midiNotes));
    assert.equal(session.stepIndex, 0, 'a paused session must not advance');
  });

  test('finishing the song reports a summary', () => {
    const song = loadSong('twinkle.mid');
    let summary = null;
    const session = new PracticeSession(song, DEFAULT_PARAMS, {
      onFinish: (s) => { summary = s; },
    });
    session.start();
    for (let i = 0; i < song.steps.length; i++) session.skip();
    assert.ok(summary, 'onFinish fired');
    assert.equal(summary.steps, song.steps.length);
  });
});

describe('the song clock', () => {
  /** Run the clock for `seconds` of real time in 16 ms frames. */
  function run(session, seconds, dt = 0.016) {
    for (let t = 0; t < seconds; t += dt) session.tick(dt);
  }

  test('starts with a lead-in, so the first note glides in', () => {
    const song = loadSong('twinkle.mid');
    const session = new PracticeSession(song, DEFAULT_PARAMS, {});
    session.start();
    assert.equal(session.songTime, song.steps[0].time - LEAD_IN_SECONDS);
  });

  test('WAIT: the clock never passes the current step, however long it runs', () => {
    const song = loadSong('twinkle.mid');
    const session = new PracticeSession(song, DEFAULT_PARAMS, {});
    session.start();

    run(session, 30);
    assert.equal(session.stepIndex, 0, 'nothing advances without a confirmed match');
    assert.ok(
      Math.abs(session.songTime - song.steps[0].time) < 1e-9,
      `clock parked at ${session.songTime}, expected ${song.steps[0].time}`,
    );
  });

  test('WAIT: confirming lets the clock travel on to the next step', () => {
    const song = loadSong('twinkle.mid');
    const session = new PracticeSession(song, DEFAULT_PARAMS, {});
    session.start();
    run(session, 5);
    session.skip();

    const target = song.steps[1].time;
    assert.ok(session.songTime < target, 'must not teleport to the next step');
    run(session, 5);
    assert.ok(Math.abs(session.songTime - target) < 1e-9, 'and then arrives');
  });

  /**
   * The regression test for the bug this whole change exists to fix: the old
   * renderer eased toward the target with a fixed time constant, so a wide gap
   * and a narrow one both took ~270 ms and the motion carried no rhythm.
   */
  test('travel time is proportional to musical distance', () => {
    const steps = groupIntoSteps([
      { midi: 60, time: 0.0, duration: 0.4, hand: 'right' },
      { midi: 62, time: 1.0, duration: 0.4, hand: 'right' }, // 1.0 s gap
      { midi: 64, time: 3.0, duration: 0.4, hand: 'right' }, // 2.0 s gap
    ]);
    const song = { name: 'synthetic', notes: [], steps };

    const timeToReach = (fromIndex) => {
      const session = new PracticeSession(song, DEFAULT_PARAMS, {});
      session.start();
      run(session, 10); // settle on step 0
      for (let i = 0; i < fromIndex; i++) {
        session.skip();
        run(session, 10);
      }
      session.skip();

      const target = steps[fromIndex + 1].time;
      let elapsed = 0;
      const dt = 0.004;
      while (session.songTime < target - 1e-9 && elapsed < 20) {
        session.tick(dt);
        elapsed += dt;
      }
      return elapsed;
    };

    const short = timeToReach(0); // 1.0 s of song
    const long = timeToReach(1); // 2.0 s of song

    assert.ok(Math.abs(short - 1.0) < 0.05, `1.0 s gap took ${short.toFixed(3)} s`);
    assert.ok(Math.abs(long - 2.0) < 0.05, `2.0 s gap took ${long.toFixed(3)} s`);
    assert.ok(long > short * 1.8, 'the wider gap must take proportionally longer');
  });

  test('tempo scales the rate', () => {
    const song = loadSong('twinkle.mid');
    const session = new PracticeSession(song, DEFAULT_PARAMS, {});
    session.setMode(PLAY);
    session.start();

    const before = session.songTime;
    run(session, 1.0);
    const atFull = session.songTime - before;

    const half = new PracticeSession(song, DEFAULT_PARAMS, {});
    half.setMode(PLAY);
    half.setTempo(0.5);
    half.start();
    const halfBefore = half.songTime;
    run(half, 1.0);
    const atHalf = half.songTime - halfBefore;

    assert.ok(Math.abs(atFull - 1.0) < 0.05, `100% advanced ${atFull.toFixed(3)} s`);
    assert.ok(Math.abs(atHalf - 0.5) < 0.05, `50% advanced ${atHalf.toFixed(3)} s`);
  });

  test('tempo is clamped to the supported range', () => {
    const song = loadSong('twinkle.mid');
    const session = new PracticeSession(song, DEFAULT_PARAMS, {});
    // A multiplier, not a percentage: 1.0 is 100%.
    session.setTempo(0.09);
    assert.equal(session.tempo, 0.25, 'clamped up to the 25% floor');
    session.setTempo(9);
    assert.equal(session.tempo, 1.5, 'clamped down to the 150% ceiling');
  });

  test('pausing stops the clock', () => {
    const song = loadSong('twinkle.mid');
    const session = new PracticeSession(song, DEFAULT_PARAMS, {});
    session.setMode(PLAY);
    session.start();
    session.togglePause();
    const before = session.songTime;
    run(session, 3);
    assert.equal(session.songTime, before);
  });
});

describe('play-along mode', () => {
  function run(session, seconds, dt = 0.016) {
    for (let t = 0; t < seconds; t += dt) session.tick(dt);
  }

  test('unplayed steps pass and are reported missed', () => {
    const song = loadSong('twinkle.mid');
    const missed = [];
    const session = new PracticeSession(song, DEFAULT_PARAMS, {
      onMiss: (step) => missed.push(step.index),
    });
    session.setMode(PLAY);
    session.start();

    run(session, 6);
    assert.ok(missed.length >= 3, `expected several misses, got ${missed.length}`);
    assert.deepEqual(missed, [...missed].sort((a, b) => a - b), 'misses arrive in order');
    assert.equal(session.confirmedCount, 0);
  });

  test('a step stays current through its grace window', () => {
    const steps = groupIntoSteps([
      { midi: 60, time: 0.0, duration: 0.4, hand: 'right' },
      { midi: 62, time: 5.0, duration: 0.4, hand: 'right' },
    ]);
    const session = new PracticeSession({ name: 's', notes: [], steps }, DEFAULT_PARAMS, {});
    session.setMode(PLAY);
    session.start();

    // Advance to just inside the grace window past step 0.
    while (session.songTime < GRACE_SECONDS * 0.5) session.tick(0.004);
    assert.equal(session.stepIndex, 0, 'still accepting the note it is late for');

    while (session.songTime < GRACE_SECONDS * 1.5) session.tick(0.004);
    assert.equal(session.stepIndex, 1, 'and gone once the grace expires');
  });

  test('a tight passage is not swallowed by the previous grace window', () => {
    // Steps 0.1 s apart — far closer than GRACE_SECONDS, so a naive
    // "step.time + grace" rule would skip straight past the middle ones.
    const steps = groupIntoSteps([
      { midi: 60, time: 0.0, duration: 0.1, hand: 'right' },
      { midi: 62, time: 0.1, duration: 0.1, hand: 'right' },
      { midi: 64, time: 0.2, duration: 0.1, hand: 'right' },
      { midi: 65, time: 0.3, duration: 0.1, hand: 'right' },
    ]);
    const seen = [];
    const session = new PracticeSession({ name: 's', notes: [], steps }, DEFAULT_PARAMS, {
      onStep: (step) => seen.push(step.index),
    });
    session.setMode(PLAY);
    session.start();
    run(session, 5, 0.004);

    assert.deepEqual(seen, [0, 1, 2, 3], 'every step must get its turn as current');
  });

  test('playing in time confirms instead of missing', () => {
    const song = loadSong('scale-and-chords.mid');
    const missed = [];
    const session = new PracticeSession(song, DEFAULT_PARAMS, {
      onMiss: (step) => missed.push(step.index),
    });
    session.setMode(PLAY);
    session.start();

    feed(session, renderStep(session.currentStep.midiNotes));
    assert.equal(session.confirmedCount, 1);
    assert.deepEqual(missed, [], 'a played note is not a missed one');
  });

  test('switching back to WAIT pulls the clock back to the current step', () => {
    const song = loadSong('twinkle.mid');
    const session = new PracticeSession(song, DEFAULT_PARAMS, {});
    session.setMode(PLAY);
    session.start();
    run(session, 3);

    session.setMode(WAIT);
    assert.ok(
      session.songTime <= session.currentStep.time + 1e-9,
      'the clock must not be left past the step the roll is waiting on',
    );
  });
});
