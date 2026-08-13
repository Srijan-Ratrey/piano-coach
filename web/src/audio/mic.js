/**
 * Microphone capture. Everything here is shaped by browser rules that silently
 * break mic apps if you get them wrong (PLAN, "Browser constraints").
 */

import { withParams } from './params.js';

/**
 * The worklet is served verbatim from `public/`, not imported as a module.
 *
 * Importing it with `?url` looks tidier but Vite inlines files under 4 KB as a
 * `data:` URL, and `audioWorklet.addModule()` accepts those inconsistently
 * across browsers — Safari in particular. A real URL always works, and the
 * worklet has no imports to bundle anyway.
 */
const WORKLET_URL = `${import.meta.env.BASE_URL}capture-worklet.js`;

export class MicError extends Error {
  constructor(message, kind) {
    super(message);
    this.name = 'MicError';
    this.kind = kind;
  }
}

/**
 * Request the microphone and start an AudioContext.
 *
 * MUST be called from inside a user gesture handler (a click or tap):
 * getUserMedia needs one, and AudioContext starts suspended and produces
 * silence until resumed inside the same gesture.
 *
 * @param {(block: Float32Array) => void} onBlock receives hop-sized blocks
 * @returns {Promise<{params, context, stop}>} params with the ACTUAL sample rate
 */
export async function startMic(baseParams, onBlock) {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new MicError('This browser does not expose getUserMedia.', 'unsupported');
  }
  if (!window.isSecureContext) {
    throw new MicError(
      'Microphone access needs HTTPS (or localhost). This page is not a secure context.',
      'insecure',
    );
  }

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        // These three OFF is not a preference. Browsers "clean" voice by
        // default: echo cancellation and noise suppression carve holes in a
        // sustained piano note, and automatic gain control defeats the
        // loudness-independence the L1-normalised chroma relies on, because it
        // changes the balance between partials over time instead of scaling
        // them equally.
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl: false,
      },
      video: false,
    });
  } catch (err) {
    if (err.name === 'NotAllowedError') {
      throw new MicError('Microphone permission was denied.', 'denied');
    }
    if (err.name === 'NotFoundError') {
      throw new MicError('No microphone was found.', 'notfound');
    }
    throw new MicError(`Could not open the microphone: ${err.message}`, 'failed');
  }

  const context = new (window.AudioContext || window.webkitAudioContext)();
  await context.resume();

  // The context picks its own rate — usually 48 kHz on macOS, but it is not
  // guaranteed, and Safari in particular may differ. Rebuild the params from
  // the rate actually in use, or every note in the band table is mistuned.
  const params = withParams(baseParams, { sampleRate: context.sampleRate });

  await context.audioWorklet.addModule(WORKLET_URL);

  const source = context.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(context, 'capture', {
    numberOfInputs: 1,
    numberOfOutputs: 0,
    processorOptions: { hop: params.hop },
  });
  node.port.onmessage = (event) => onBlock(event.data);
  source.connect(node);

  const label = stream.getAudioTracks()[0]?.label ?? 'unknown input';

  return {
    params,
    context,
    deviceLabel: label,
    async stop() {
      node.port.onmessage = null;
      try { source.disconnect(); } catch { /* already gone */ }
      for (const track of stream.getTracks()) track.stop();
      await context.close();
    },
  };
}
