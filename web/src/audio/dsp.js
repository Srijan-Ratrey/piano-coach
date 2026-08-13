/**
 * Framing and spectrum — the JS side of spike/core/dsp.py.
 *
 * Must reproduce numpy's `abs(rfft(x * hann))` to within 1e-6. Two things carry
 * almost all the port risk:
 *
 *  1. The Hann window is PERIODIC (divisor N), not symmetric (divisor N-1).
 *     numpy.hanning gives the symmetric form. Getting this wrong shifts every
 *     magnitude slightly and silently invalidates every tuned threshold.
 *
 *  2. numpy's rfft is UNSCALED — no 1/N anywhere. Many JS FFT implementations
 *     normalise the forward transform. This one does not.
 *
 * Both are pinned by test/golden.test.js.
 */

const windowCache = new Map();

/** Periodic Hann: 0.5 - 0.5*cos(2*pi*k/n), k in [0, n). Divisor n, not n-1. */
export function hannPeriodic(n) {
  let w = windowCache.get(n);
  if (!w) {
    w = new Float64Array(n);
    for (let k = 0; k < n; k++) w[k] = 0.5 - 0.5 * Math.cos((2 * Math.PI * k) / n);
    windowCache.set(n, w);
  }
  return w;
}

const twiddleCache = new Map();

function twiddles(n) {
  let t = twiddleCache.get(n);
  if (!t) {
    const cos = new Float64Array(n / 2);
    const sin = new Float64Array(n / 2);
    for (let i = 0; i < n / 2; i++) {
      cos[i] = Math.cos((-2 * Math.PI * i) / n);
      sin[i] = Math.sin((-2 * Math.PI * i) / n);
    }
    t = { cos, sin };
    twiddleCache.set(n, t);
  }
  return t;
}

/**
 * In-place iterative radix-2 Cooley-Tukey FFT, unscaled forward transform.
 * `re` and `im` are Float64Array of length n, n a power of two.
 */
export function fftInPlace(re, im) {
  const n = re.length;
  if (n !== im.length) throw new Error('re/im length mismatch');
  if ((n & (n - 1)) !== 0) throw new Error(`fft size ${n} is not a power of two`);

  // Bit-reversal permutation.
  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      let t = re[i]; re[i] = re[j]; re[j] = t;
      t = im[i]; im[i] = im[j]; im[j] = t;
    }
  }

  const { cos, sin } = twiddles(n);
  for (let len = 2; len <= n; len <<= 1) {
    const step = n / len;
    const half = len >> 1;
    for (let i = 0; i < n; i += len) {
      for (let k = 0; k < half; k++) {
        const tw = k * step;
        const wr = cos[tw];
        const wi = sin[tw];
        const a = i + k;
        const b = a + half;
        const xr = re[b] * wr - im[b] * wi;
        const xi = re[b] * wi + im[b] * wr;
        re[b] = re[a] - xr;
        im[b] = im[a] - xi;
        re[a] += xr;
        im[a] += xi;
      }
    }
  }
}

/**
 * Windowed magnitude spectrum of one frame: fftSize/2 + 1 non-negative values.
 *
 * Magnitudes, not power — chroma sums them linearly, and squaring would
 * over-weight the loudest partial and change what the harmonic weights mean.
 */
export function spectrum(frame, fftSize, scratch) {
  const n = fftSize ?? frame.length;
  if (frame.length !== n) throw new Error(`frame length ${frame.length} != fftSize ${n}`);

  const re = scratch?.re ?? new Float64Array(n);
  const im = scratch?.im ?? new Float64Array(n);
  const out = scratch?.mag ?? new Float64Array(n / 2 + 1);

  const w = hannPeriodic(n);
  for (let i = 0; i < n; i++) {
    re[i] = frame[i] * w[i];
    im[i] = 0;
  }

  fftInPlace(re, im);

  for (let k = 0; k <= n / 2; k++) out[k] = Math.hypot(re[k], im[k]);
  return out;
}

/** Reusable buffers, so the per-frame path allocates nothing. */
export function makeScratch(fftSize) {
  return {
    re: new Float64Array(fftSize),
    im: new Float64Array(fftSize),
    mag: new Float64Array(fftSize / 2 + 1),
  };
}

export function rms(frame) {
  if (frame.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < frame.length; i++) sum += frame[i] * frame[i];
  return Math.sqrt(sum / frame.length);
}

export function clippedFraction(samples, threshold) {
  if (samples.length === 0) return 0;
  let n = 0;
  for (let i = 0; i < samples.length; i++) if (Math.abs(samples[i]) >= threshold) n++;
  return n / samples.length;
}
