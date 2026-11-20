"""A song: MIDI-karaoke (.kar) or a MIDI file with lyrics, read with mido.

What the skill needs from any .kar file:

  * the lyrics as syllables with their times, and where words, lines and
    verses start ("Soft Karaoke": text events in their own track, '/' new
    line, '\\' new verse, a leading space a new word; or the standard
    MIDI lyric events, FF 05);
  * which track is the melody - a .kar file doesn't say, so it is the track
    whose notes start where the syllables do;
  * the notes each syllable is sung on (several notes = a melisma);
  * the chords, for the screen - found from the other tracks' notes.

Times are in seconds (the tempo map applied), so the singer, the band and
the screen don't need to know about ticks.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import mido

DRUM_CHANNEL = 9


@dataclass
class Note:
    start: float
    end: float
    pitch: int


@dataclass
class Syllable:
    time: float
    text: str                  # without markers or the leading space
    word_start: bool
    line_start: bool
    verse_start: bool
    notes: list[Note] = field(default_factory=list)   # sung on (empty: spoken/unknown)

    @property
    def end(self) -> float:
        return self.notes[-1].end if self.notes else self.time + 0.3


@dataclass
class Chord:
    time: float
    name: str


@dataclass
class Song:
    title: str
    language: str
    syllables: list[Syllable]
    chords: list[Chord]
    midi: mido.MidiFile
    melody_track: int | None
    length: float

    @property
    def lines(self) -> list[list[Syllable]]:
        out = []
        for s in self.syllables:
            if s.line_start or not out:
                out.append([])
            out[-1].append(s)
        return out

    def words(self, line: list[Syllable]) -> list[list[Syllable]]:
        out = []
        for s in line:
            if s.word_start or not out:
                out.append([])
            out[-1].append(s)
        return out


# ------------------------------------------------------------------ reading
def _tempo_map(mid: mido.MidiFile):
    """[(tick, seconds_at_tick, tempo)] from all tracks' tempo events."""
    changes = []
    for tr in mid.tracks:
        t = 0
        for m in tr:
            t += m.time
            if m.type == "set_tempo":
                changes.append((t, m.tempo))
    changes.sort()
    out, sec, last_tick, tempo = [(0, 0.0, 500000)], 0.0, 0, 500000
    for tick, tp in changes:
        sec += mido.tick2second(tick - last_tick, mid.ticks_per_beat, tempo)
        out.append((tick, sec, tp))
        last_tick, tempo = tick, tp
    return out


def _seconds(tmap, tpb):
    def conv(tick):
        i = max(k for k in range(len(tmap)) if tmap[k][0] <= tick)
        t0, s0, tp = tmap[i]
        return s0 + mido.tick2second(tick - t0, tpb, tp)
    return conv


def _decode(m) -> str:
    if hasattr(m, "text"):
        t = m.text
    else:
        t = ""
    try:                        # mido decodes as latin-1; most .kar are cp1252
        t = t.encode("latin-1").decode("cp1252")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return t


def _lyric_events(mid: mido.MidiFile, sec):
    """The syllables, from the track that has the most of them."""
    best = []
    for tr in mid.tracks:
        for kind in ("text", "lyrics"):
            ev, t = [], 0
            for m in tr:
                t += m.time
                if m.type == kind:
                    txt = _decode(m)
                    if kind == "text" and txt.startswith("@"):   # .kar header info
                        continue
                    ev.append((sec(t), txt))
            # a few text events are titles/credits, not lyrics
            if len(ev) > len(best) and len(ev) >= 8:
                best = ev
    return best


def _parse_syllables(events) -> list[Syllable]:
    out = []
    pending_line, pending_verse, pending_word = True, True, True
    for t, raw in events:
        txt = raw.replace("\r\n", "\n")
        if txt.strip() == "" and ("\n" in txt or "\r" in txt):
            pending_line = True
            continue
        if txt.startswith("\\"):
            pending_verse = pending_line = pending_word = True
            txt = txt[1:]
        elif txt.startswith("/"):
            pending_line = pending_word = True
            txt = txt[1:]
        if txt[:1] in ("\n", "\r"):
            pending_line = pending_word = True
        txt_s = txt.strip("\r\n")
        if txt_s.startswith(" "):
            pending_word = True
        ends_line = txt.endswith(("\n", "\r"))
        ends_word = txt_s.endswith(" ")
        word = txt_s.strip()
        if not word:
            if ends_line:
                pending_line = pending_word = True
            elif txt_s:
                pending_word = True
            continue
        if word.endswith("-"):          # "Dai-" style hyphenation (FF 05 files)
            word = word[:-1]
            ends_word = False
        out.append(Syllable(t, word, pending_word or pending_line, pending_line, pending_verse))
        pending_line = pending_verse = False
        pending_word = ends_word or ends_line
        if ends_line:
            pending_line = True
    return out


def _track_notes(mid: mido.MidiFile, sec):
    """{track: [Note]} for melodic tracks (drums left out)."""
    out = {}
    for i, tr in enumerate(mid.tracks):
        t, on, notes = 0, {}, []
        for m in tr:
            t += m.time
            if m.type == "note_on" and m.velocity > 0 and m.channel != DRUM_CHANNEL:
                on[(m.channel, m.note)] = t
            elif m.type in ("note_off", "note_on") and getattr(m, "channel", None) != DRUM_CHANNEL:
                k = (m.channel, m.note)
                if k in on:
                    notes.append(Note(sec(on.pop(k)), sec(t), m.note))
        if notes:
            out[i] = sorted(notes, key=lambda n: (n.start, -n.pitch))
    return out


def _melody_track(tracks, syllables, tol=0.08):
    """The track whose notes start where the syllables are sung."""
    if not syllables:
        return None
    best, best_score = None, 0.0
    for i, notes in tracks.items():
        starts = sorted({round(n.start, 3) for n in notes})
        hits = 0
        j = 0
        for s in syllables:
            while j < len(starts) and starts[j] < s.time - tol:
                j += 1
            if j < len(starts) and abs(starts[j] - s.time) <= tol:
                hits += 1
        # chords on every syllable also "hit": prefer one note at a time
        poly = len(notes) / max(1, len(starts))
        score = hits / len(syllables) / poly ** 0.5
        if score > best_score:
            best, best_score = i, score
    return best if best_score >= 0.5 else None


def _top_line(notes):
    """One note at a time: the highest of notes starting together."""
    out = []
    for n in notes:
        if out and abs(out[-1].start - n.start) < 0.02:
            continue
        if out and out[-1].end > n.start:
            out[-1] = Note(out[-1].start, n.start, out[-1].pitch)
        out.append(n)
    return out


def _assign_notes(syllables, melody, tol=0.12):
    """Each syllable gets the melody notes from its time to the next one's."""
    for k, s in enumerate(syllables):
        nxt = syllables[k + 1].time if k + 1 < len(syllables) else float("inf")
        mine = [n for n in melody if s.time - tol <= n.start < nxt - tol]
        if not mine:          # sung over a note that started before (a tie)
            mine = [n for n in melody if n.start <= s.time < n.end]
        s.notes = mine


CHORD_SHAPES = {"": (0, 4, 7), "m": (0, 3, 7), "7": (0, 4, 7, 10), "maj7": (0, 4, 7, 11),
                "m7": (0, 3, 7, 10), "dim": (0, 3, 6)}
NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


def detect_chords(tracks, melody_idx, length, step):
    """Name the harmony every `step` seconds from the accompaniment's
    notes; the lowest note counts extra (it is usually the root)."""
    notes = [n for i, ns in tracks.items() if i != melody_idx for n in ns]
    if not notes:
        return []
    out, t = [], 0.0
    while t < length:
        w = [0.0] * 12
        low = None
        for n in notes:
            ov = min(n.end, t + step) - max(n.start, t)
            if ov > 0:
                w[n.pitch % 12] += ov
                if low is None or n.pitch < low:
                    low = n.pitch
        top = max(w)
        if top > 0:
            best, score = None, -1e9
            for root in range(12):
                for q, shape in CHORD_SHAPES.items():
                    tones = [(root + x) % 12 for x in shape]
                    inn = sum(w[x] for x in tones)
                    outn = sum(w) - inn
                    missing = sum(max(0.0, 0.35 * top - w[x]) for x in tones)
                    sc = inn - 1.5 * outn - 1.2 * missing - 0.05 * len(shape) * top
                    if low is not None and low % 12 == root:
                        sc += 0.4 * top
                    if sc > score:
                        best, score = NAMES[root] + q, sc
            if not out or out[-1].name != best:
                out.append(Chord(t, best))
        t += step
    return out


def load(data: bytes, title: str = "", language: str = "") -> Song:
    mid = mido.MidiFile(file=io.BytesIO(data))
    tmap = _tempo_map(mid)
    sec = _seconds(tmap, mid.ticks_per_beat)
    for tr in mid.tracks:                     # .kar headers: @T title, @L language
        for m in tr:
            if m.type == "text":
                txt = _decode(m)
                if txt.startswith("@T") and not title:
                    title = txt[2:].strip()
                elif txt.startswith("@L") and not language:
                    language = txt[2:].strip().lower()[:2]
    syllables = _parse_syllables(_lyric_events(mid, sec))
    tracks = _track_notes(mid, sec)
    mel = _melody_track(tracks, syllables)
    if mel is not None:
        _assign_notes(syllables, _top_line(tracks[mel]))
    beat = 60.0 / mido.tempo2bpm(tmap[-1][2])
    meter = next((m.numerator for tr in mid.tracks for m in tr if m.type == "time_signature"), 4)
    # chords usually change per bar or half bar: name them per half bar in
    # even meters, per bar in odd ones (3/4)
    step = beat * (meter / 2 if meter % 2 == 0 else meter)
    length = mid.length
    chords = detect_chords(tracks, mel, length, step)
    lang = {"en": "en", "eng": "en", "da": "da", "dan": "da"}.get(language[:3], language[:2] or "en")
    return Song(title or "", lang, syllables, chords, mid, mel, length)


# ------------------------------------------------------------------ writing
def kar_words_track(syllables_ticks, title: str, language: str, credits: str = ""):
    """A Soft Karaoke "Words" track: [(tick, text, word_start, line_start, verse_start)]."""
    tr = mido.MidiTrack()
    tr.append(mido.MetaMessage("track_name", name="Words", time=0))
    head = ["@KMIDI KARAOKE FILE", "@V0100", f"@L{language.upper()[:4]}", f"@T{title}"]
    if credits:
        head.append(f"@T{credits}")
    for h in head:
        tr.append(mido.MetaMessage("text", text=h, time=0))
    last = 0
    for tick, text, ws, ls, vs in syllables_ticks:
        prefix = "\\" if vs else "/" if ls else " " if ws else ""
        tr.append(mido.MetaMessage("text", text=prefix + text, time=tick - last))
        last = tick
    tr.append(mido.MetaMessage("end_of_track", time=0))
    return tr


def clean_word(text: str) -> str:
    return re.sub(r"[^\w'’-]+", "", text)
