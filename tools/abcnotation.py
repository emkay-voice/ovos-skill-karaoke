"""Minimal ABC parser for the songbook sources (tools/songs/*.abc).

Supports the subset the songbook uses: header fields (T, C, M, L, Q, K),
notes with accidentals/octaves/lengths, ties, rests, bar lines, chord
symbols ("D", "A7", "Bm") and aligned lyrics (w:) with '-', '_' and '*',
plus two comments of our own: %%language and %%style (the arrangement).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from fractions import Fraction

NOTE_BASE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
SHARP_ORDER = "FCGDAEB"
MAJOR_SHARPS = {"C": 0, "G": 1, "D": 2, "A": 3, "E": 4, "B": 5, "F#": 6,
                "F": -1, "Bb": -2, "Eb": -3, "Ab": -4, "Db": -5, "Gb": -6}


@dataclass
class Note:
    start: float          # in beats (quarter notes)
    dur: float            # in beats
    midi: int | None      # None = rest
    syllable: str | None = None
    word_end: bool = True
    melisma: bool = False  # continues previous syllable ('_')


@dataclass
class Chord:
    start: float
    name: str


@dataclass
class Song:
    title: str = ""
    composer: str = ""
    language: str = "en"
    style: str = "waltz"
    meter: tuple[int, int] = (4, 4)
    tempo_bpm: float = 120.0
    notes: list[Note] = field(default_factory=list)
    chords: list[Chord] = field(default_factory=list)

    @property
    def beats_per_bar(self) -> float:
        n, d = self.meter
        return n * 4 / d

    @property
    def length_beats(self) -> float:
        return max(n.start + n.dur for n in self.notes)


def _key_accidentals(key: str) -> dict[str, int]:
    key = key.strip().split()[0] if key.strip() else "C"
    minor = key.endswith("m") and not key.endswith("maj")
    root = key[:-1] if minor else key
    if minor:  # relative major
        semis = (NOTE_BASE[root[0]] + (1 if "#" in root else -1 if "b" in root else 0) + 3) % 12
        root = {0: "C", 7: "G", 2: "D", 9: "A", 4: "E", 11: "B", 6: "F#",
                5: "F", 10: "Bb", 3: "Eb", 8: "Ab", 1: "Db"}[semis]
    n = MAJOR_SHARPS.get(root, 0)
    acc = {}
    if n > 0:
        for c in SHARP_ORDER[:n]:
            acc[c] = 1
    elif n < 0:
        for c in SHARP_ORDER[::-1][: -n]:
            acc[c] = -1
    return acc


NOTE_RE = re.compile(r"(\^\^|\^|__|_|=)?([A-Ga-gz])([,']*)(\d*)(/*)(\d*)(-?)")


def _length(num: str, slashes: str, den: str) -> Fraction:
    n = Fraction(int(num)) if num else Fraction(1)
    if slashes:
        d = int(den) if den else 2 ** len(slashes)
        n /= d
    return n


def _lyric_tokens(line: str) -> list[tuple[str, str]]:
    """Return (kind, text): kind in syl/hold/skip; syl text ends with '-' if word continues."""
    out = []
    for tok in re.findall(r"[^\s\-_*]+-?|_|\*", line.replace("~", " ")):
        if tok == "_":
            out.append(("hold", ""))
        elif tok == "*":
            out.append(("skip", ""))
        else:
            out.append(("syl", tok))
    return out


def parse(text: str) -> Song:
    song = Song()
    unit = Fraction(1, 8)
    key_acc: dict[str, int] = {}
    t = Fraction(0)          # in whole notes
    bar_acc: dict[tuple[str, int], int] = {}
    pending_tie = False
    lyrics: list[tuple[str, str]] = []

    for raw in text.splitlines():
        line = raw.split("%", 1)[0].rstrip() if not raw.startswith("%%") else ""
        if raw.startswith("%%language"):
            song.language = raw.split()[1]
        if raw.startswith("%%style"):
            song.style = raw.split()[1]
        if not line.strip():
            continue
        if re.match(r"^[A-Za-z]:", line) and not line.startswith("w:"):
            f, v = line[0], line[2:].strip()
            if f == "T" and not song.title:
                song.title = v
            elif f == "C":
                song.composer = v
            elif f == "M":
                a, b = v.split("/")
                song.meter = (int(a), int(b))
            elif f == "L":
                a, b = v.split("/")
                unit = Fraction(int(a), int(b))
            elif f == "Q":
                bpm = v.split("=")[-1]
                song.tempo_bpm = float(bpm)
            elif f == "K":
                key_acc = _key_accidentals(v)
            continue
        if line.startswith("w:"):
            lyrics.extend(_lyric_tokens(line[2:]))
            continue
        i = 0
        while i < len(line):
            c = line[i]
            if c == '"':
                j = line.index('"', i + 1)
                song.chords.append(Chord(float(t * 4), line[i + 1:j]))
                i = j + 1
                continue
            if c in "|:[]":
                bar_acc = {}
                i += 1
                continue
            m = NOTE_RE.match(line, i)
            if not m:
                i += 1
                continue
            acc, letter, octs, num, slashes, den, tie = m.groups()
            dur = _length(num, slashes, den) * unit
            if letter == "z":
                song.notes.append(Note(float(t * 4), float(dur * 4), None))
                pending_tie = False
            else:
                up = letter.upper()
                octave = 5 if letter.islower() else 4
                octave += octs.count("'") - octs.count(",")
                if acc:
                    a = {"^": 1, "^^": 2, "_": -1, "__": -2, "=": 0}[acc]
                    bar_acc[(up, octave)] = a
                a = bar_acc.get((up, octave), key_acc.get(up, 0))
                midi = 12 * (octave + 1) + NOTE_BASE[up] + a
                if pending_tie and song.notes and song.notes[-1].midi == midi:
                    song.notes[-1].dur += float(dur * 4)
                else:
                    song.notes.append(Note(float(t * 4), float(dur * 4), midi))
                pending_tie = bool(tie)
            t += dur
            i = m.end()

    # attach lyrics to sung notes
    li = 0
    for n in song.notes:
        if n.midi is None:
            continue
        if li >= len(lyrics):
            break
        kind, txt = lyrics[li]
        li += 1
        if kind == "skip":
            continue
        if kind == "hold":
            n.melisma = True
            continue
        n.word_end = not txt.endswith("-")
        n.syllable = txt.rstrip("-")
    return song
