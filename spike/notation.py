"""Parsing note names off the command line and out of the corpus manifest.

Accepts sharps or flats (`C#` and `Db` are the same pitch class) because the
corpus script is written in whichever spelling reads most naturally for the
chord in question, and forcing one spelling would make the manifest harder to
check by eye.
"""

from __future__ import annotations

import re

from .core.chroma import PITCH_CLASS_NAMES

_SEMITONE = {
    "C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11,
}

_NOTE_RE = re.compile(r"^\s*([A-Ga-g])([#b♯♭]?)(-?\d+)?\s*$")


def parse_pitch_class(text: str) -> int:
    """`'C'`, `'c#'`, `'Db'`, and `'C#4'` all parse; the octave is ignored."""
    m = _NOTE_RE.match(text)
    if not m:
        raise ValueError(f"cannot parse note name {text!r}")
    letter, accidental, _ = m.groups()
    pc = _SEMITONE[letter.upper()]
    if accidental in ("#", "♯"):
        pc += 1
    elif accidental in ("b", "♭"):
        pc -= 1
    return pc % 12


def parse_note(text: str) -> int:
    """`'C4'` -> 60. Requires an octave; middle C is C4 (MIDI 60)."""
    m = _NOTE_RE.match(text)
    if not m:
        raise ValueError(f"cannot parse note name {text!r}")
    letter, accidental, octave = m.groups()
    if octave is None:
        raise ValueError(f"note {text!r} needs an octave, e.g. C4")
    pc = parse_pitch_class(letter + accidental)
    return pc + 12 * (int(octave) + 1)


def parse_target(text: str, octave_mode: bool) -> tuple[int, ...]:
    """Parse a comma-separated target such as `'C,E,G'` or `'C4,E4,G4'`.

    In pitch-class mode duplicates collapse, which is correct: a chord voiced
    with C in two octaves still asks the verifier one question about C.
    """
    parts = [p for p in re.split(r"[,\s]+", text.strip()) if p]
    if not parts:
        raise ValueError("empty target")
    if octave_mode:
        return tuple(sorted({parse_note(p) for p in parts}))
    return tuple(sorted({parse_pitch_class(p) for p in parts}))


def format_target(target: tuple[int, ...], octave_mode: bool) -> str:
    from .core.chroma import note_name

    if octave_mode:
        return " ".join(note_name(m) for m in target)
    return " ".join(PITCH_CLASS_NAMES[pc] for pc in target)
