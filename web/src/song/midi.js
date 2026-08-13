/**
 * MIDI -> steps. PLAN Feature 2, and open decision D.
 *
 * A "step" is one thing the player has to play: a single note or a chord.
 * Notes starting within `GROUP_MS` of each other are one step, because nobody
 * strikes a chord's notes at literally the same millisecond and the verifier
 * has no way to tell an intentional chord from three fast single notes anyway.
 */

import * as midiModule from '@tonejs/midi';

/**
 * @tonejs/midi ships an ESM build under "module" and a CommonJS build under
 * "main", with no "exports" map to reconcile them. Vite resolves the ESM one
 * and gets a named export; bare Node resolves the CommonJS one and does not.
 * Reading through both shapes lets the same source run under the bundler and
 * under `node --test`, which is what keeps the headless session tests possible.
 */
const Midi = midiModule.Midi ?? midiModule.default?.Midi ?? midiModule.default;

export const GROUP_MS = 50;

export const RIGHT = 'right';
export const LEFT = 'left';

/**
 * Which hand plays a note.
 *
 * Real scores put each hand on its own track, so track order is used when the
 * file provides it. Many MIDI files found in the wild are a single merged
 * track, in which case splitting at middle C is the standard rough guess — it
 * is wrong for crossed hands and wide left-hand passages, but it is display
 * only and never affects verification.
 */
function assignHands(tracks) {
  const withNotes = tracks.filter((t) => t.notes.length > 0);
  if (withNotes.length >= 2) {
    // Higher average pitch is the right hand, rather than assuming track order.
    const avg = withNotes.map((t) => ({
      track: t,
      mean: t.notes.reduce((s, n) => s + n.midi, 0) / t.notes.length,
    }));
    avg.sort((a, b) => b.mean - a.mean);
    const rightTracks = new Set([avg[0].track]);
    return (note, track) => (rightTracks.has(track) ? RIGHT : LEFT);
  }
  return (note) => (note.midi >= 60 ? RIGHT : LEFT);
}

/**
 * Fingering heuristic — display only, never verified.
 *
 * DECISIONS is explicit that a microphone cannot detect fingers, so this is
 * guidance and nothing more. It is also only a heuristic: real fingering
 * depends on the whole phrase, and a teacher would often choose differently.
 *
 * Two rules, applied per hand across the whole song:
 *
 *   Chords — thumb on the innermost note (lowest for the right hand, highest
 *   for the left), then outward. This is what hands actually do.
 *
 *   Melody — follow the contour. Step up, next finger up; step down, next
 *   finger down. Running past the fifth finger means a thumb-under, so it
 *   resets to 1; running below the thumb means crossing a finger over, so it
 *   resets to 3. That is what produces 1-2-3-4-5-1-2-3 on a rising scale
 *   rather than a column of 1s.
 *
 * Numbering within a single step would give every note of a melody the number
 * 1, which is not fingering at all — just a decoration that happens to be
 * printed on every bar.
 */
function assignChordFingers(notes, hand) {
  const sorted = [...notes].sort((a, b) =>
    hand === RIGHT ? a.midi - b.midi : b.midi - a.midi,
  );
  sorted.forEach((note, i) => {
    note.finger = Math.min(5, i + 1);
  });
  return sorted;
}

function assignFingering(steps, hand) {
  let prevMidi = null;
  let prevFinger = null;

  for (const step of steps) {
    const notes = step.notes.filter((n) => n.hand === hand);
    if (notes.length === 0) continue;

    if (notes.length > 1) {
      const sorted = assignChordFingers(notes, hand);
      // Continue the melodic line from the thumb note.
      prevMidi = sorted[0].midi;
      prevFinger = sorted[0].finger;
      continue;
    }

    const note = notes[0];
    let finger;

    if (prevMidi === null) {
      finger = 1;
    } else {
      // Positive means "outward from the thumb" for this hand.
      const delta = hand === RIGHT ? note.midi - prevMidi : prevMidi - note.midi;
      const direction = Math.sign(delta);

      if (delta === 0) {
        finger = prevFinger;
      } else if (Math.abs(delta) <= 2) {
        finger = prevFinger + direction;
      } else if (Math.abs(delta) <= 4) {
        finger = prevFinger + direction * 2;
      } else {
        // A leap wide enough that the hand repositions entirely; land on the
        // middle finger, which leaves room to move either way afterwards.
        finger = 3;
      }

      if (finger > 5) finger = 1; // thumb passes under
      if (finger < 1) finger = 3; // a finger crosses over the thumb
    }

    note.finger = finger;
    prevMidi = note.midi;
    prevFinger = finger;
  }
}

export function parseMidi(arrayBuffer, name = 'song') {
  const midi = new Midi(arrayBuffer);
  const handOf = assignHands(midi.tracks);

  const notes = [];
  for (const track of midi.tracks) {
    for (const n of track.notes) {
      notes.push({
        midi: n.midi,
        time: n.time,
        duration: n.duration,
        velocity: n.velocity,
        hand: handOf(n, track),
      });
    }
  }
  notes.sort((a, b) => a.time - b.time || a.midi - b.midi);

  return {
    name: midi.name || name,
    durationSeconds: midi.duration,
    notes,
    steps: groupIntoSteps(notes),
  };
}

/** Group near-simultaneous notes into steps (decision D). */
export function groupIntoSteps(notes, groupMs = GROUP_MS) {
  const steps = [];
  const window = groupMs / 1000;

  for (const note of notes) {
    const last = steps[steps.length - 1];
    if (last && note.time - last.time <= window) {
      last.notes.push(note);
    } else {
      steps.push({ time: note.time, notes: [note] });
    }
  }

  // Fingering runs across the whole song, per hand, before the per-step
  // summaries — it depends on what came before, not just on the step.
  assignFingering(steps, RIGHT);
  assignFingering(steps, LEFT);

  steps.forEach((step, index) => {
    step.index = index;
    step.midiNotes = [...new Set(step.notes.map((n) => n.midi))].sort((a, b) => a - b);
    step.pitchClasses = [...new Set(step.midiNotes.map((m) => m % 12))].sort((a, b) => a - b);
    step.endTime = Math.max(...step.notes.map((n) => n.time + n.duration));
  });

  return steps;
}

/**
 * Filter a song to one hand, or keep both.
 * DECISIONS C recommends single-hand first; this makes that a runtime choice
 * rather than a rebuild.
 */
export function filterByHand(song, hand) {
  if (hand === 'both') return song;
  const notes = song.notes.filter((n) => n.hand === hand);
  return { ...song, notes, steps: groupIntoSteps(notes) };
}

/**
 * The target the verifier is asked about for a step.
 *
 * Notes outside the analysed MIDI range still contribute their pitch class, so
 * a very low bass note does not make its step unverifiable — it just loses
 * octave information, which pitch-class mode was never using.
 */
export function targetForStep(step, params) {
  if (params.octaveMode) {
    return step.midiNotes.filter((m) => m >= params.midiLow && m <= params.midiHigh);
  }
  return step.pitchClasses;
}

export function songRange(song) {
  if (!song.notes.length) return { min: 60, max: 72 };
  let min = Infinity;
  let max = -Infinity;
  for (const n of song.notes) {
    if (n.midi < min) min = n.midi;
    if (n.midi > max) max = n.midi;
  }
  return { min, max };
}
