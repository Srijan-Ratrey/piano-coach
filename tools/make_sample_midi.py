"""Write the sample MIDI files the web app ships with.

    uv run python tools/make_sample_midi.py web/public/midi

All public domain. A hand-rolled writer rather than a dependency: standard MIDI
files are simple enough that adding a library to emit four short tunes is not
worth it, and this keeps the sample set reproducible from source rather than
being opaque binaries in the repo.

Two tracks per file (right hand, left hand) so the app's hand-splitting takes
the track path rather than the pitch-guess fallback.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

TICKS_PER_BEAT = 480


def _var_len(value: int) -> bytes:
    """MIDI variable-length quantity."""
    out = bytearray([value & 0x7F])
    value >>= 7
    while value:
        out.insert(0, (value & 0x7F) | 0x80)
        value >>= 7
    return bytes(out)


def _track(events: list[tuple[int, bytes]], tempo_bpm: int | None = None) -> bytes:
    """Build a track chunk from (absolute_tick, message) pairs."""
    data = bytearray()

    if tempo_bpm is not None:
        us_per_beat = int(60_000_000 / tempo_bpm)
        data += _var_len(0) + b"\xff\x51\x03" + us_per_beat.to_bytes(3, "big")

    last = 0
    for tick, message in sorted(events, key=lambda e: e[0]):
        data += _var_len(tick - last) + message
        last = tick

    data += _var_len(0) + b"\xff\x2f\x00"  # end of track
    return b"MTrk" + struct.pack(">I", len(data)) + bytes(data)


def _notes_to_events(notes, channel: int, velocity: int = 80):
    """`notes` is a list of (start_beats, length_beats, midi)."""
    events = []
    for start, length, midi in notes:
        on = int(round(start * TICKS_PER_BEAT))
        off = int(round((start + length) * TICKS_PER_BEAT))
        events.append((on, bytes([0x90 | channel, midi, velocity])))
        events.append((off, bytes([0x80 | channel, midi, 0])))
    return events


def write_midi(path: Path, right, left, tempo_bpm: int = 90) -> None:
    header = b"MThd" + struct.pack(">IHHH", 6, 1, 2, TICKS_PER_BEAT)
    tracks = [
        _track(_notes_to_events(right, channel=0), tempo_bpm=tempo_bpm),
        _track(_notes_to_events(left, channel=1, velocity=68)),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + b"".join(tracks))


# --- the tunes -------------------------------------------------------------
# (start_in_beats, length_in_beats, midi_note)

C = 60


def n(name: str) -> int:
    """Tiny helper so the tunes below read like music, e.g. n('E4')."""
    base = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
    letter, rest = name[0].upper(), name[1:]
    semitone = base[letter]
    if rest and rest[0] in "#b":
        semitone += 1 if rest[0] == "#" else -1
        rest = rest[1:]
    return semitone + 12 * (int(rest) + 1)


def scale_and_chords():
    """The simplest possible warm-up: a C major scale, then three triads.

    Deliberately first in the list — it is the fastest way to see whether
    detection works at all, and its notes sit in the register the spike is most
    confident about.
    """
    right = []
    t = 0.0
    for note in ("C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5"):
        right.append((t, 0.9, n(note)))
        t += 1.0
    for chord in (
        ("C4", "E4", "G4"),
        ("F4", "A4", "C5"),
        ("G4", "B4", "D5"),
        ("C4", "E4", "G4"),
    ):
        for note in chord:
            right.append((t, 1.8, n(note)))
        t += 2.0

    left = []
    t = 0.0
    for note in ("C3", "C3", "C3", "C3"):
        left.append((t, 1.8, n(note)))
        t += 2.0
    for root in ("C3", "F2", "G2", "C3"):
        left.append((t, 1.8, n(root)))
        t += 2.0
    return right, left


def ode_to_joy():
    """Beethoven, 1824. Public domain."""
    melody = [
        ("E4", 1),
        ("E4", 1),
        ("F4", 1),
        ("G4", 1),
        ("G4", 1),
        ("F4", 1),
        ("E4", 1),
        ("D4", 1),
        ("C4", 1),
        ("C4", 1),
        ("D4", 1),
        ("E4", 1),
        ("E4", 1.5),
        ("D4", 0.5),
        ("D4", 2),
        ("E4", 1),
        ("E4", 1),
        ("F4", 1),
        ("G4", 1),
        ("G4", 1),
        ("F4", 1),
        ("E4", 1),
        ("D4", 1),
        ("C4", 1),
        ("C4", 1),
        ("D4", 1),
        ("E4", 1),
        ("D4", 1.5),
        ("C4", 0.5),
        ("C4", 2),
    ]
    right = []
    t = 0.0
    for name, length in melody:
        right.append((t, length * 0.92, n(name)))
        t += length

    bass = [
        "C3",
        "C3",
        "G2",
        "C3",
        "C3",
        "C3",
        "G2",
        "C3",
        "C3",
        "C3",
        "G2",
        "C3",
        "C3",
        "C3",
        "G2",
        "C3",
    ]
    left = [(i * 2.0, 1.8, n(name)) for i, name in enumerate(bass)]
    return right, left


def twinkle():
    """Traditional, public domain. Melody plus simple triads underneath."""
    melody = [
        ("C4", 1),
        ("C4", 1),
        ("G4", 1),
        ("G4", 1),
        ("A4", 1),
        ("A4", 1),
        ("G4", 2),
        ("F4", 1),
        ("F4", 1),
        ("E4", 1),
        ("E4", 1),
        ("D4", 1),
        ("D4", 1),
        ("C4", 2),
    ]
    right = []
    t = 0.0
    for name, length in melody:
        right.append((t, length * 0.9, n(name)))
        t += length

    chords = [
        ("C3", "E3", "G3"),
        ("C3", "F3", "A3"),
        ("C3", "F3", "A3"),
        ("C3", "E3", "G3"),
        ("B2", "D3", "F3"),
        ("C3", "E3", "G3"),
        ("B2", "D3", "G3"),
        ("C3", "E3", "G3"),
    ]
    left = []
    for i, chord in enumerate(chords):
        for name in chord:
            left.append((i * 2.0, 1.8, n(name)))
    return right, left


def chord_drill():
    """Four-note chords with gaps — the case the spike flags as hardest.

    Included precisely because it is unflattering: dense chords spread the
    normalised chroma thinner and collide more overtones, so this is where a
    threshold tuned on triads shows its limits.
    """
    chords = [
        ("C3", "E3", "G3", "C4"),
        ("F3", "A3", "C4", "F4"),
        ("G3", "B3", "D4", "G4"),
        ("C3", "E3", "G3", "C4"),
        ("A2", "C3", "E3", "A3"),
        ("D3", "F3", "A3", "D4"),
        ("G2", "B2", "D3", "G3"),
        ("C3", "E3", "G3", "C4"),
    ]
    right, left = [], []
    for i, chord in enumerate(chords):
        t = i * 2.0
        for name in chord:
            midi = n(name)
            (right if midi >= 60 else left).append((t, 1.6, midi))
    return right, left


SONGS = {
    "scale-and-chords": (scale_and_chords, 88, "C major scale, then triads"),
    "ode-to-joy": (ode_to_joy, 96, "Beethoven, 1824"),
    "twinkle": (twinkle, 92, "Traditional"),
    "chord-drill": (chord_drill, 76, "Four-note chords — the hard case"),
}


def main(argv: list[str]) -> int:
    out = Path(argv[1]) if len(argv) > 1 else Path("web/public/midi")
    index = []
    for slug, (build, tempo, note) in SONGS.items():
        right, left = build()
        path = out / f"{slug}.mid"
        write_midi(path, right, left, tempo)
        index.append({"slug": slug, "file": f"{slug}.mid", "note": note})
        print(f"wrote {path}  ({len(right)} right, {len(left)} left, {tempo} bpm)")

    import json

    (out / "index.json").write_text(json.dumps(index, indent=2) + "\n")
    print(f"wrote {out / 'index.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
