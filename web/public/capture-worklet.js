/**
 * AudioWorklet that hands raw hop-sized blocks of microphone audio to the main
 * thread. No analysis happens here.
 *
 * Why a worklet rather than AnalyserNode: AnalyserNode applies its own
 * smoothing and dB conversion and does not expose its windowing, so it cannot
 * reproduce the golden vectors. Owning the raw samples means the browser runs
 * exactly the transform the Python spike measured.
 *
 * Loaded as a raw URL, so this file must stay dependency-free — it runs in the
 * AudioWorkletGlobalScope, which has no module resolution and no DOM.
 */

class CaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.hop = options?.processorOptions?.hop ?? 2048;
    this.buffer = new Float32Array(this.hop);
    this.filled = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0) return true;
    const channel = input[0];
    if (!channel) return true;

    // The render quantum (128 frames) does not divide evenly into the hop, so
    // blocks are accumulated here and emitted only when a full hop is ready.
    // That keeps the main thread's frame timing exact rather than approximate.
    for (let i = 0; i < channel.length; i++) {
      this.buffer[this.filled++] = channel[i];
      if (this.filled === this.hop) {
        this.port.postMessage(this.buffer.slice());
        this.filled = 0;
      }
    }
    return true;
  }
}

registerProcessor('capture', CaptureProcessor);
