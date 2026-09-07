/**
 * The input monitor exists to separate "not recognised" from "nothing arriving",
 * so the tests are mostly about it reaching the RIGHT conclusion rather than
 * merely reporting numbers.
 */

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import { DEFAULT_PARAMS } from '../src/audio/params.js';
import {
  CLIPPING,
  InputMonitor,
  OK,
  QUIET,
  SILENT,
  WINDOW_SECONDS,
} from '../src/audio/input-monitor.js';

const HOP = DEFAULT_PARAMS.hop;

function feed(monitor, fill, blocks = 20) {
  for (let b = 0; b < blocks; b++) {
    const block = new Float32Array(HOP);
    for (let i = 0; i < HOP; i++) block[i] = fill(i, b);
    monitor.push(block);
  }
}

const tone = (amp) => (i) => amp * Math.sin((2 * Math.PI * 440 * i) / DEFAULT_PARAMS.sampleRate);

describe('input monitor', () => {
  test('withholds a verdict until enough audio has arrived', () => {
    const m = new InputMonitor(DEFAULT_PARAMS);
    assert.equal(m.ready, false);
    // An empty monitor must not claim SILENT — at startup there is simply no
    // evidence yet, and crying wolf on frame one would train the warning away.
    assert.equal(m.verdict, OK);
    feed(m, tone(0.3), 8);
    assert.equal(m.ready, true);
  });

  test('digital silence reads as SILENT', () => {
    const m = new InputMonitor(DEFAULT_PARAMS);
    feed(m, () => 0);
    assert.equal(m.verdict, SILENT);
    assert.match(m.advice, /No audio arriving/);
  });

  test('a muted-but-live input still reads as SILENT', () => {
    // The real failure mode: permission granted, device delivering dither
    // rather than a clean zero. A literal `peak === 0` test would miss it.
    const m = new InputMonitor(DEFAULT_PARAMS);
    let seed = 7;
    feed(m, () => {
      seed = (seed * 1103515245 + 12345) & 0x7fffffff;
      return ((seed / 0x7fffffff) - 0.5) * 2e-5;
    });
    assert.equal(m.verdict, SILENT);
  });

  test('a healthy signal reads as OK', () => {
    const m = new InputMonitor(DEFAULT_PARAMS);
    feed(m, tone(0.3));
    assert.equal(m.verdict, OK);
    assert.equal(m.advice, '');
  });

  test('an audible but sub-gate signal reads as QUIET', () => {
    const m = new InputMonitor(DEFAULT_PARAMS);
    feed(m, tone(0.002));
    assert.equal(m.verdict, QUIET);
    assert.ok(m.rms < DEFAULT_PARAMS.silenceRms, 'below the verifier’s own gate');
  });

  test('clipping is reported, and outranks quiet', () => {
    const m = new InputMonitor(DEFAULT_PARAMS);
    feed(m, (i) => (i % 2 ? 1.0 : -1.0));
    assert.equal(m.verdict, CLIPPING);
    assert.ok(m.clippedFraction > 0.9);
  });

  test('even a trace of clipping wins over an otherwise fine level', () => {
    // Distortion manufactures harmonics that read as extra notes, so it blocks
    // matches outright. It must not be masked by a healthy average level.
    const m = new InputMonitor(DEFAULT_PARAMS);
    feed(m, (i, b) => (b === 5 && i < 40 ? 1.0 : 0.3 * Math.sin(i / 7)));
    assert.equal(m.verdict, CLIPPING);
  });

  test('the window forgets, so a fixed problem stops being reported', () => {
    const m = new InputMonitor(DEFAULT_PARAMS);
    feed(m, () => 0, m.maxBlocks);
    assert.equal(m.verdict, SILENT);
    feed(m, tone(0.3), m.maxBlocks);
    assert.equal(m.verdict, OK, 'stale silence must not linger in the window');
  });

  test('the window is about the documented duration', () => {
    const m = new InputMonitor(DEFAULT_PARAMS);
    const seconds = (m.maxBlocks * HOP) / DEFAULT_PARAMS.sampleRate;
    assert.ok(Math.abs(seconds - WINDOW_SECONDS) < 0.1, `window is ${seconds}s`);
  });

  test('thresholds are taken from params, not hardcoded', () => {
    const m = new InputMonitor(DEFAULT_PARAMS);
    assert.equal(m.clipThreshold, DEFAULT_PARAMS.clipThreshold);
    assert.equal(m.silenceRms, DEFAULT_PARAMS.silenceRms);
  });
});
