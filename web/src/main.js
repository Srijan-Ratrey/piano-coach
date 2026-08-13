/**
 * App shell: landing -> session, and the glue between microphone, wait-mode
 * loop and renderer.
 */

import { DEFAULT_PARAMS } from './audio/params.js';
import { startMic, MicError } from './audio/mic.js';
import { PITCH_CLASS_NAMES } from './audio/chroma.js';
import { filterByHand, parseMidi } from './song/midi.js';
import { PianoRoll } from './render/pianoroll.js';
import { WaitModeSession } from './session/waitmode.js';

const $ = (id) => document.getElementById(id);

const state = {
  catalogue: [],
  selected: null, // {name, note, buffer}
  hand: 'right',
  mic: null,
  session: null,
  roll: null,
  raf: null,
  lastTick: 0,
};

// --- Landing ---------------------------------------------------------------

async function loadCatalogue() {
  try {
    const res = await fetch('/midi/index.json');
    if (!res.ok) throw new Error(String(res.status));
    state.catalogue = await res.json();
  } catch {
    state.catalogue = [];
  }
  renderSongList();
}

function renderSongList() {
  const list = $('song-list');
  list.innerHTML = '';

  if (!state.catalogue.length) {
    const li = document.createElement('li');
    li.className = 'hint';
    li.textContent = 'No bundled songs found — open a .mid file below.';
    list.append(li);
    return;
  }

  for (const entry of state.catalogue) {
    const li = document.createElement('li');
    const button = document.createElement('button');
    button.setAttribute('aria-pressed', 'false');
    button.innerHTML =
      `<span class="name">${prettify(entry.slug)}</span>` +
      `<span class="note">${entry.note}</span>`;
    button.addEventListener('click', () => selectBundled(entry, button));
    li.append(button);
    list.append(li);
  }
}

function prettify(slug) {
  return slug.replace(/-/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

async function selectBundled(entry, button) {
  const res = await fetch(`/midi/${entry.file}`);
  const buffer = await res.arrayBuffer();
  select({ name: prettify(entry.slug), note: entry.note, buffer });

  for (const b of document.querySelectorAll('#song-list button')) {
    b.setAttribute('aria-pressed', String(b === button));
  }
}

function select(song) {
  state.selected = song;
  $('start-mic').disabled = false;
  $('start-silent').disabled = false;

  // Parse now rather than at start, so a malformed file fails here — while the
  // user is still looking at the list — instead of on a black session screen.
  try {
    const parsed = parseMidi(song.buffer, song.name);
    const steps = filterByHand(parsed, state.hand).steps.length;
    $('start-hint').textContent =
      `${song.name} — ${parsed.notes.length} notes, ${steps} steps for the ${state.hand} hand.`;
    $('mic-error').hidden = true;
  } catch (err) {
    $('start-hint').textContent = '';
    showError(`Could not read that MIDI file.\n${err.message}`);
    $('start-mic').disabled = true;
    $('start-silent').disabled = true;
  }
}

function showError(message) {
  const el = $('mic-error');
  el.textContent = message;
  el.hidden = false;
}

// --- Session ---------------------------------------------------------------

function buildMeter() {
  const meter = $('meter');
  meter.innerHTML = '';
  return PITCH_CLASS_NAMES.map((name) => {
    const bin = document.createElement('div');
    bin.className = 'bin';
    bin.title = name;
    meter.append(bin);
    return bin;
  });
}

let meterBins = [];

async function beginSession({ useMic }) {
  const parsed = parseMidi(state.selected.buffer, state.selected.name);
  const song = filterByHand(parsed, state.hand);

  if (!song.steps.length) {
    showError(`That song has no notes for the ${state.hand} hand. Try another hand.`);
    return;
  }

  let params = DEFAULT_PARAMS;

  if (useMic) {
    try {
      state.mic = await startMic(DEFAULT_PARAMS, (block) => {
        state.session?.pushAudio(block);
      });
      params = state.mic.params;
    } catch (err) {
      if (err instanceof MicError) {
        showError(
          `${err.message}\n\n` +
            'You can still use "Practice without microphone" to see the roll and ' +
            'advance with the spacebar.',
        );
      } else {
        showError(String(err));
      }
      return;
    }
  }

  $('view-landing').hidden = true;
  $('view-session').hidden = false;

  meterBins = buildMeter();
  $('song-title').textContent = song.name + (useMic ? '' : '  ·  no microphone');

  state.roll = new PianoRoll($('roll'), song);
  state.session = new WaitModeSession(song, params, {
    onStep: (step, { verifiable }) => {
      state.roll.setStep(step.index);
      updateProgress();
      if (!verifiable) {
        setStatus('extra', 'Out of detectable range — skip it');
      } else if (!useMic) {
        setStatus('waiting', 'Press space to advance');
      }
    },
    onConfirm: (step) => {
      state.roll.markMatched(step.midiNotes);
    },
    onFrame: (frame) => updateMeter(frame),
    onFinish: (summary) => {
      setStatus('ok', `Finished — ${summary.confirmed}/${summary.steps} played`);
    },
  });

  state.session.start();
  checkOrientation();

  state.lastTick = performance.now();
  const loop = (now) => {
    const dt = Math.min(0.05, (now - state.lastTick) / 1000);
    state.lastTick = now;
    state.roll.tick(dt);
    state.roll.draw();
    state.raf = requestAnimationFrame(loop);
  };
  state.raf = requestAnimationFrame(loop);
}

function updateProgress() {
  const s = state.session;
  if (!s) return;
  $('progress').textContent = `step ${s.stepIndex + 1} / ${s.song.steps.length}`;
}

function setStatus(kind, text) {
  $('status-dot').className = `status-dot ${kind}`;
  $('status-text').textContent = text;
}

function updateMeter(frame) {
  const chroma = frame.chroma;
  let peak = 0;
  for (let i = 0; i < 12; i++) if (chroma[i] > peak) peak = chroma[i];

  const verdict = frame.verdict;
  const targets = new Set(state.session?.currentStep?.pitchClasses ?? []);
  const extras = new Set((verdict?.extras ?? []).map((e) => e % 12));

  for (let i = 0; i < 12; i++) {
    const bin = meterBins[i];
    const h = peak > 0 ? Math.max(2, (chroma[i] / peak) * 26) : 2;
    bin.style.height = `${h}px`;
    bin.className = 'bin' + (targets.has(i) ? ' target' : extras.has(i) ? ' extra' : '');
  }

  if (!verdict) return;
  if (verdict.silent) setStatus('', 'Listening…');
  else if (!verdict.armed) setStatus('waiting', 'Waiting for you to play');
  else if (verdict.extras.length) {
    const names = verdict.extras.map((e) => PITCH_CLASS_NAMES[e % 12]).join(' ');
    setStatus('extra', `Extra note: ${names}`);
  } else if (!verdict.targetsPresent) setStatus('waiting', 'Not quite — try again');
  else setStatus('holding', `Holding ${verdict.framesHeld}/${state.session.analyser.verifier?.stabilityFrames ?? 6}`);
}

async function endSession() {
  cancelAnimationFrame(state.raf);
  state.raf = null;
  await state.mic?.stop();
  state.mic = null;
  state.session = null;
  state.roll = null;
  $('view-session').hidden = true;
  $('view-landing').hidden = false;
}

// --- Orientation and lifecycle ---------------------------------------------

function checkOrientation() {
  // Rotation cannot be forced from a web page, so a portrait phone gets a hint
  // rather than a broken layout (PLAN, browser constraints B3).
  const portrait = window.innerHeight > window.innerWidth && window.innerWidth < 720;
  $('rotate-hint').hidden = !portrait;
}

window.addEventListener('resize', () => {
  state.roll?.resize();
  checkOrientation();
});

document.addEventListener('visibilitychange', () => {
  // Browsers throttle timers and audio in background tabs, which would corrupt
  // the frame stream. Suspend cleanly rather than analysing garbage.
  if (!state.mic) return;
  if (document.hidden) state.mic.context.suspend();
  else state.mic.context.resume();
});

// --- Wiring ----------------------------------------------------------------

for (const button of document.querySelectorAll('#hand-select button')) {
  button.addEventListener('click', () => {
    state.hand = button.dataset.hand;
    for (const b of document.querySelectorAll('#hand-select button')) {
      b.classList.toggle('on', b === button);
      b.setAttribute('aria-checked', String(b === button));
    }
    if (state.selected) select(state.selected);
  });
}

$('file-input').addEventListener('change', async (event) => {
  const file = event.target.files?.[0];
  if (!file) return;
  select({ name: file.name.replace(/\.midi?$/i, ''), note: 'your file', buffer: await file.arrayBuffer() });
  for (const b of document.querySelectorAll('#song-list button')) {
    b.setAttribute('aria-pressed', 'false');
  }
});

// Both start buttons are real user gestures, which is what getUserMedia and
// AudioContext.resume() require (PLAN, browser constraints B1/B2).
$('start-mic').addEventListener('click', () => beginSession({ useMic: true }));
$('start-silent').addEventListener('click', () => beginSession({ useMic: false }));

$('quit').addEventListener('click', endSession);
$('skip').addEventListener('click', () => state.session?.skip());
$('back').addEventListener('click', () => state.session?.back());
$('restart').addEventListener('click', () => state.session?.restart());

window.addEventListener('keydown', (event) => {
  if ($('view-session').hidden) return;
  if (event.code === 'Space') {
    event.preventDefault();
    state.session?.skip();
  } else if (event.code === 'ArrowLeft') {
    state.session?.back();
  } else if (event.code === 'Escape') {
    endSession();
  }
});

/**
 * Deep link: ?song=twinkle&hand=right
 *
 * Only ever starts the no-microphone mode. A page cannot open the microphone
 * without a user gesture (PLAN, browser constraints B1/B2), and pretending
 * otherwise would fail in a confusing way — so the link opens the roll and the
 * player presses the button themselves if they want to be listened to.
 */
async function applyDeepLink() {
  const query = new URLSearchParams(location.search);
  const slug = query.get('song');
  if (!slug) return;

  const hand = query.get('hand');
  if (hand && ['right', 'left', 'both'].includes(hand)) {
    document.querySelector(`#hand-select button[data-hand="${hand}"]`)?.click();
  }

  const entry = state.catalogue.find((e) => e.slug === slug);
  if (!entry) {
    showError(`No bundled song called "${slug}".`);
    return;
  }
  const button = [...document.querySelectorAll('#song-list button')].find((b) =>
    b.textContent.startsWith(prettify(slug)),
  );
  await selectBundled(entry, button);
  await beginSession({ useMic: false });
}

loadCatalogue().then(applyDeepLink);
