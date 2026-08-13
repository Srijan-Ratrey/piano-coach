"""Magnitude spectrum → per-note energies → 12-bin chroma vector.

This is PLAN's Feature 1 step 3, and the place where the project's core
technical risk lives: "a low note's overtones mimic higher notes". A piano C2
radiates strong energy at C3, G3, C4, E4 — so a naive "is there energy at E4?"
test says yes when nobody touched E4.

Two mitigations, both from PLAN/DECISIONS:

- Each *note* accumulates its own fundamental plus a few harmonics, with the
  fundamental weighted highest. A note that is genuinely sounding has energy at
  its own fundamental; a note merely implied by a lower note's overtone series
  usually does not.
- Verification additionally requires the *absence* of loud unexpected pitch
  classes (`verify.py`), which is what stops the residual leakage from reading
  as a correct answer.

Both the folded 12-bin chroma and the un-folded per-note energies are returned,
so open decision E (pitch-class-only vs exact-octave verification) can be
settled by measurement rather than by preference.
"""

from __future__ import annotations

import numpy as np

from ..params import Params

A4_MIDI = 69
A4_HZ = 440.0


def midi_to_hz(midi: int | np.ndarray) -> float | np.ndarray:
    """Equal-tempered pitch of a MIDI note number, A4 = 440 Hz."""
    return A4_HZ * np.power(2.0, (np.asarray(midi, dtype=np.float64) - A4_MIDI) / 12.0)


class ChromaExtractor:
    """Precomputes the note→bin lookup table once, then reuses it per frame.

    Holding this as a table rather than recomputing frequencies each frame is
    both a speed win and a port aid: the JS version builds the identical table
    at startup, so the two implementations can be compared table-first before
    any audio is involved.
    """

    def __init__(self, params: Params):
        self.params = params
        self.notes = np.arange(params.midi_low, params.midi_high + 1, dtype=int)
        self.note_hz = midi_to_hz(self.notes)

        self.n_bins = params.fft_size // 2 + 1
        nyquist_bin = self.n_bins - 1
        bin_hz = params.bin_hz
        cents = params.neighbourhood_cents
        cap = params.max_neighbour_bins

        # A flat list of (note_index, weight, lo_bin, hi_bin) search bands, one
        # per (note, harmonic) pair that lands inside the analysed band. Built
        # once; the per-frame loop just peak-picks each band.
        #
        # The band is a constant *ratio* around the harmonic's frequency, so its
        # width in bins grows with pitch — narrow enough in the bass that
        # adjacent semitones stay separate, wide enough in the treble to tolerate
        # tuning drift.
        bands: list[tuple[int, float, int, int]] = []
        self.harmonic_bins = np.full(
            (len(self.notes), len(params.harmonic_weights)), -1, dtype=int
        )

        for h, weight in enumerate(params.harmonic_weights, start=1):
            freqs = self.note_hz * h
            centres = np.rint(freqs / bin_hz).astype(int)
            # Round the edges to nearest rather than outward. Rounding outward
            # (floor/ceil) always widens the band to at least two bins and
            # usually three, which re-introduces exactly the neighbour overlap
            # the cents window exists to remove. Nearest-rounding keeps only the
            # bins whose centres actually fall inside the tolerance.
            lows = np.rint(freqs * 2.0 ** (-cents / 1200.0) / bin_hz).astype(int)
            highs = np.rint(freqs * 2.0 ** (cents / 1200.0) / bin_hz).astype(int)

            # Never wider than the cap, and never narrower than the single
            # nearest bin — a band that rounded to nothing would silently drop
            # that harmonic's contribution.
            lows = np.maximum(lows, centres - cap)
            highs = np.minimum(highs, centres + cap)
            lows = np.minimum(lows, centres)
            highs = np.maximum(highs, centres)

            for i in range(len(self.notes)):
                centre = int(centres[i])
                if centre < 1 or centre > nyquist_bin:
                    continue
                lo = max(1, int(lows[i]))
                hi = min(nyquist_bin, int(highs[i]))
                if hi < lo:
                    continue
                self.harmonic_bins[i, h - 1] = centre
                bands.append((i, float(weight), lo, hi + 1))

        self.bands = bands
        self.weights = np.asarray(params.harmonic_weights, dtype=np.float64)

        # Pitch class of each note, for the fold at the end.
        self.pitch_classes = self.notes % 12

    def band_width_bins(self, midi: int, harmonic: int = 1) -> int:
        """Width of the search band, for inspection and tests."""
        i = self.note_index(midi)
        for note_i, _, lo, hi in self.bands:
            if note_i == i:
                if harmonic == 1:
                    return hi - lo
                harmonic -= 1
        raise ValueError(f"no band for MIDI {midi} harmonic {harmonic}")

    def note_energies(self, spec: np.ndarray) -> np.ndarray:
        """Weighted harmonic energy for every note in range.

        Each harmonic contributes the peak magnitude within its search band.
        Peak rather than sum: a partial occupies one or two bins and the rest of
        the band is noise, so summing would reward wide bands for containing
        more noise.
        """
        out = np.zeros(len(self.notes), dtype=np.float64)
        for note_i, weight, lo, hi in self.bands:
            out[note_i] += weight * spec[lo:hi].max()
        return out

    def chroma(self, spec: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Return ``(chroma12, note_energies)``, both L1-normalised.

        L1 normalisation is what makes the vector loudness-independent: playing
        the same chord softly or loudly gives the same chroma, so a single
        threshold works across dynamics without an automatic gain stage. Silent
        frames return all-zeros rather than dividing by zero — callers gate on
        RMS before trusting a frame anyway.
        """
        notes = self.note_energies(spec)

        chroma = np.zeros(12, dtype=np.float64)
        np.add.at(chroma, self.pitch_classes, notes)

        chroma_sum = chroma.sum()
        if chroma_sum > 0:
            chroma = chroma / chroma_sum

        note_sum = notes.sum()
        if note_sum > 0:
            notes = notes / note_sum

        return chroma, notes

    def note_index(self, midi: int) -> int:
        """Position of a MIDI note within the `note_energies` vector."""
        if not (self.params.midi_low <= midi <= self.params.midi_high):
            raise ValueError(
                f"MIDI {midi} outside analysed range "
                f"[{self.params.midi_low}, {self.params.midi_high}]"
            )
        return midi - self.params.midi_low


PITCH_CLASS_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def pitch_class_name(pc: int) -> str:
    return PITCH_CLASS_NAMES[pc % 12]


def note_name(midi: int) -> str:
    """e.g. 60 -> 'C4'. Octave numbering follows the scientific convention
    where middle C (MIDI 60) is C4, matching the reference marker described in
    DECISIONS ('Note names: reference marker (e.g. C4) at the keyboard')."""
    return f"{PITCH_CLASS_NAMES[midi % 12]}{midi // 12 - 1}"
