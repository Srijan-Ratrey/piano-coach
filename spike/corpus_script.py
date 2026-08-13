"""The recording script: what to play, and what each item is there to prove.

Chosen so the corpus measures the *failure modes* PLAN and DECISIONS call out,
not just the easy cases. A corpus of ten clean mid-register triads would score
beautifully and tell us nothing, because nothing in this project is expected to
fail on clean mid-register triads.

Every item states the question it answers. If an item cannot say what it would
disprove, it should not be in the corpus.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .core.chroma import note_name


@dataclass(frozen=True)
class Item:
    id: str
    name: str
    played: tuple[int, ...]
    """MIDI notes the player actually strikes. Empty for room tone."""

    target: tuple[int, ...]
    """MIDI notes the verifier is *asked* about. Differs from `played` for the
    negative controls — that difference is the whole point of them."""

    kind: str
    """'positive' — must confirm; 'negative' — must not confirm."""

    register: str
    """'bass' | 'mid' | 'treble' | 'mixed' — drives the per-register breakdown
    that makes a CONSTRAINED GO verdict possible."""

    why: str
    instruction: str
    pedal: bool = False
    dynamics: tuple[str, ...] = ("soft", "medium", "loud")
    takes: int = 1

    modes: tuple[str, ...] = ("pitch_class", "octave")
    """Verification modes this item is meaningful under.

    Not every item asks a sensible question in both. A target of C4+C5 folds to
    the single pitch class C, so in pitch-class mode 'play C4 alone against a
    C4+C5 target' is not a negative control at all — it is a tautology that
    must confirm, and scoring it would charge the detector with a false confirm
    for behaving correctly. Items like that are restricted to octave mode."""

    @property
    def played_names(self) -> str:
        return " ".join(note_name(m) for m in self.played) if self.played else "—"

    @property
    def target_names(self) -> str:
        return " ".join(note_name(m) for m in self.target) if self.target else "—"


HOLD = "Wait one beat of silence, then strike and hold until it says STOP."
HOLD_PEDAL = "Press the sustain pedal FIRST and keep it down for the whole take."

SCRIPT: tuple[Item, ...] = (
    # --- per-register baseline --------------------------------------------
    Item(
        id="c2_single",
        name="C2 alone",
        played=(36,),
        target=(36,),
        kind="positive",
        register="bass",
        why=(
            "The bass resolution test. At 5.86 Hz bins a semitone near C2 is "
            "narrower than one bin, so this is the item most likely to fail. "
            "If it does, the honest answer is a constrained GO on register, "
            "not a rewrite."
        ),
        instruction=HOLD,
    ),
    Item(
        id="c4_single",
        name="C4 alone (middle C)",
        played=(60,),
        target=(60,),
        kind="positive",
        register="mid",
        why="The easiest case in the corpus. If this fails, something is broken, not marginal.",
        instruction=HOLD,
    ),
    Item(
        id="c6_single",
        name="C6 alone",
        played=(84,),
        target=(84,),
        kind="positive",
        register="treble",
        why=(
            "High notes have few harmonics inside the analysis band and short "
            "sustain, so they fail for the opposite reason to bass notes: not "
            "enough energy held for long enough."
        ),
        instruction=HOLD,
    ),
    # --- ordinary chords ---------------------------------------------------
    Item(
        id="c_major",
        name="C major triad",
        played=(60, 64, 67),
        target=(60, 64, 67),
        kind="positive",
        register="mid",
        why="The ordinary case the app spends most of its time on.",
        instruction=HOLD,
    ),
    Item(
        id="f_major",
        name="F major triad",
        played=(53, 57, 60),
        target=(53, 57, 60),
        kind="positive",
        register="mid",
        why="A second ordinary chord, lower voicing, to check the result is not C-specific.",
        instruction=HOLD,
    ),
    Item(
        id="a_minor",
        name="A minor triad",
        played=(57, 60, 64),
        target=(57, 60, 64),
        kind="positive",
        register="mid",
        why=(
            "Shares C and E with C major. Confirms the verifier is reading the "
            "chord rather than pattern-matching whatever it saw last."
        ),
        instruction=HOLD,
    ),
    Item(
        id="g7",
        name="G7 (four notes)",
        played=(55, 59, 62, 65),
        target=(55, 59, 62, 65),
        kind="positive",
        register="mid",
        why=(
            "Four notes spread the normalised chroma thinner and collide more "
            "overtones. This is where a threshold tuned on triads breaks."
        ),
        instruction=HOLD,
    ),
    # --- the ambiguity cases ----------------------------------------------
    Item(
        id="neighbour_c4_d4",
        name="C4 + D4 together",
        played=(60, 62),
        target=(60, 62),
        kind="positive",
        register="mid",
        why=(
            "Adjacent keys, a whole tone apart. Tests frequency resolution "
            "directly rather than through a chord's harmonic structure."
        ),
        instruction=HOLD,
    ),
    Item(
        id="octave_c4_c5",
        name="C4 + C5 together",
        played=(60, 72),
        target=(60, 72),
        kind="positive",
        register="mid",
        why=(
            "Open decision E in one item. In pitch-class mode this is "
            "indistinguishable from C4 alone; in octave mode it must resolve "
            "both. Scoring it under both modes is what settles the decision."
        ),
        instruction=HOLD,
    ),
    Item(
        id="octave_c4_only",
        name="C4 alone, scored against C4+C5",
        played=(60,),
        target=(60, 72),
        kind="negative",
        register="mid",
        why=(
            "The companion to the item above, and the sharper half of decision "
            "E: C4's second harmonic IS C5. If the verifier confirms a C4+C5 "
            "target when only C4 is played, octave mode is decorative. "
            "Octave mode only — in pitch-class mode the target folds to plain "
            "C and the item becomes a tautology."
        ),
        instruction=HOLD,
        dynamics=("medium",),
        takes=3,
        modes=("octave",),
    ),
    # --- sustain pedal (decision F) ---------------------------------------
    Item(
        id="pedal_ring_only",
        name="Pedal down, C major ringing, asked for F major",
        played=(60, 64, 67),
        target=(53, 57, 60),
        kind="negative",
        register="mid",
        why=(
            "The runaway-advance failure in its purest form. A ringing C major "
            "must not satisfy an F major step. If this confirms, wait-mode "
            "fast-forwards through songs on its own."
        ),
        instruction=HOLD_PEDAL + " Play C major and let it ring. Do NOT play F.",
        pedal=True,
        dynamics=("medium",),
        takes=3,
    ),
    Item(
        id="pedal_c_then_f",
        name="Pedal down, C major then F major",
        played=(53, 57, 60),
        target=(53, 57, 60),
        kind="positive",
        register="mid",
        why=(
            "The hardest positive in the corpus, and the realistic one. C is "
            "still ringing under F, so E and G linger as candidate extras. "
            "Measures whether onset gating plus extra-note rejection can "
            "co-exist, or whether they have to be traded off."
        ),
        instruction=(
            HOLD_PEDAL + " Play C major, let it ring one second, then play F "
            "major and hold. Keep the pedal down throughout."
        ),
        pedal=True,
        dynamics=("medium", "loud"),
    ),
    # --- negative controls -------------------------------------------------
    Item(
        id="wrong_chord",
        name="F major played, C major asked",
        played=(53, 57, 60),
        target=(60, 64, 67),
        kind="negative",
        register="mid",
        why=(
            "A plainly wrong chord that shares one pitch class (C) with the "
            "target. Rejecting a totally unrelated chord is easy; rejecting a "
            "near miss is the test."
        ),
        instruction=HOLD,
        dynamics=("medium",),
        takes=3,
    ),
    Item(
        id="room_tone",
        name="Room tone (play nothing)",
        played=(),
        target=(60, 64, 67),
        kind="negative",
        register="mid",
        why=(
            "The noise floor must never confirm. Also records what the room "
            "and mic contribute before any note is played, which sets a lower "
            "bound on how quiet the silence gate can be."
        ),
        instruction="Play nothing at all. Stay still and quiet until STOP.",
        dynamics=("medium",),
        takes=3,
    ),
)

BY_ID = {item.id: item for item in SCRIPT}


def total_takes(script: tuple[Item, ...] = SCRIPT, extra_takes: int = 0) -> int:
    return sum(len(i.dynamics) * (i.takes + extra_takes) for i in script)


@dataclass
class PlannedTake:
    item: Item
    dynamic: str
    take: int
    index: int = field(default=0)

    @property
    def stem(self) -> str:
        return f"{self.item.id}__{self.dynamic}__take{self.take}"


def plan(script: tuple[Item, ...] = SCRIPT, extra_takes: int = 0) -> list[PlannedTake]:
    """Flatten the script into an ordered list of takes to record."""
    out: list[PlannedTake] = []
    for item in script:
        for dynamic in item.dynamics:
            for take in range(1, item.takes + extra_takes + 1):
                out.append(PlannedTake(item=item, dynamic=dynamic, take=take))
    for i, p in enumerate(out, start=1):
        p.index = i
    return out
