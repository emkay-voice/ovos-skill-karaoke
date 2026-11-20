"""Turn the songbook's ABC sources into MIDI-karaoke (.kar) files.

    python tools/arrange.py            # all of tools/songs/*.abc

A source holds the melody, the chords and the lyrics; the arrangement (band,
groove) comes from its %%style. The result is an ordinary .kar file - any
karaoke player can play it - with General MIDI instruments:

  track 0  tempo, meter, title
  track 1  Words: the lyrics, Soft Karaoke style
  track 2  Melody (the guide the karaoke mode plays; muted when Emkay sings)
  track 3+ the band

Songs listed in OFFLINE are written to songs/ (shipped with the skill), the
rest to songbook/ (fetched when asked for).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import mido

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT))

from abcnotation import parse  # noqa: E402

TPB = 480
OFFLINE = {"daisy_bell", "hey_emkay"}
INTRO_BARS = 2

NAMES = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5, "F#": 6,
         "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}


def chord_pcs(name):
    root = name[:2] if len(name) > 1 and name[1] in "#b" else name[:1]
    q = name[len(root):]
    r = NAMES[root]
    if q.startswith("m") and not q.startswith("maj"):
        iv = [0, 3, 7]
    else:
        iv = [0, 4, 7]
    if "maj7" in q:
        iv.append(11)
    elif "7" in q:
        iv.append(10)
    return r, iv


def voicing(name, low=55):
    """Chord tones from `low` upwards (close position)."""
    r, iv = chord_pcs(name)
    out = []
    for x in iv:
        p = low + ((r + x - low) % 12)
        out.append(p)
    return sorted(out)


def bass_note(name, low=36):
    r, _ = chord_pcs(name)
    return low + ((r - low) % 12)


class Part:
    def __init__(self, name, channel, program):
        self.name, self.ch, self.prog = name, channel, program
        self.ev = []            # (tick, on/off, pitch, velocity)

    def note(self, start, dur, pitch, vel):
        a, b = int(round(start * TPB)), int(round((start + dur) * TPB))
        if b > a:
            self.ev.append((a, 1, pitch, vel))
            self.ev.append((b, 0, pitch, 0))

    def track(self):
        tr = mido.MidiTrack()
        tr.append(mido.MetaMessage("track_name", name=self.name, time=0))
        if self.ch != 9:
            tr.append(mido.Message("program_change", channel=self.ch, program=self.prog, time=0))
        last = 0
        for t, on, p, v in sorted(self.ev, key=lambda e: (e[0], e[1])):
            kind = "note_on" if on else "note_off"
            tr.append(mido.Message(kind, channel=self.ch, note=p, velocity=v, time=t - last))
            last = t
        tr.append(mido.MetaMessage("end_of_track", time=0))
        return tr


def chord_spans(song, offset, end):
    """[(start_beat, end_beat, chord)] with the first chord also in the intro."""
    ch = sorted(song.chords, key=lambda c: c.start)
    spans = [(0, offset, ch[0].name)]
    for i, c in enumerate(ch):
        e = ch[i + 1].start if i + 1 < len(ch) else end - offset
        spans.append((c.start + offset, e + offset, c.name))
    return spans


# ---------------------------------------------------------------- styles
def waltz(song, spans, bpb):
    bass, piano, acc, strings = (Part("Bass", 1, 32), Part("Piano", 2, 0),
                                 Part("Accordion", 3, 21), Part("Strings", 4, 48))
    for a, b, name in spans:
        t = a
        while t < b - 1e-6:
            bass.note(t, 0.9, bass_note(name, 40), 92)
            for p in voicing(name, 57):
                piano.note(t + 1, 0.6, p, 62)
                piano.note(t + 2, 0.6, p, 56)
                acc.note(t + 1, 0.5, p + 12, 38)
                acc.note(t + 2, 0.5, p + 12, 34)
            t += bpb
        for p in voicing(name, 60):
            strings.note(a, b - a, p, 40)
    return [bass, piano, acc, strings], 73      # flute guide melody


def sleigh(song, spans, bpb):
    drums, bass, piano, brass, glock = (Part("Drums", 9, 0), Part("Bass", 1, 32),
                                        Part("Piano", 2, 0), Part("Brass", 3, 61),
                                        Part("Glockenspiel", 4, 9))
    end = spans[-1][1]
    t = 0
    while t < end - 1e-6:
        drums.note(t, 0.25, 36, 90)
        drums.note(t + 1, 0.25, 38, 70)
        drums.note(t + 2, 0.25, 36, 80)
        drums.note(t + 3, 0.25, 38, 72)
        for k in range(8):                       # sleigh bells on every eighth
            drums.note(t + k * 0.5, 0.25, 83, 78 if k % 2 == 0 else 58)
        t += bpb
    for a, b, name in spans:
        r = bass_note(name, 40)
        third = r + 3 if name[1:2] == "m" else r + 4
        walk = [r, third, r + 7, third]
        t, k = a, 0
        while t < b - 1e-6:
            bass.note(t, 0.9, walk[k % 4], 88)
            if k % 2 == 1:
                for p in voicing(name, 57):
                    piano.note(t, 0.45, p, 66)
                    piano.note(t + 0.5, 0.3, p, 50)
            t += 1
            k += 1
        for p in voicing(name, 58):
            brass.note(a, 0.4, p, 70)
    for n in song.notes:
        if n.midi is not None:
            glock.note(n.start + INTRO_BARS * bpb, min(n.dur, 1.0), n.midi + 12, 44)
    return [drums, bass, piano, brass, glock], 71   # clarinet guide melody


def disco(song, spans, bpb):
    drums, bass, clav, strings, brass = (Part("Drums", 9, 0), Part("Bass", 1, 38),
                                         Part("Clavinet", 2, 7), Part("Strings", 3, 48),
                                         Part("Brass", 4, 62))
    end = spans[-1][1]
    t = 0
    while t < end - 1e-6:
        for beat in range(4):
            drums.note(t + beat, 0.25, 36, 104)                     # four on the floor
            drums.note(t + beat + 0.5, 0.25, 46, 74)               # open hi-hat off-beat
            drums.note(t + beat, 0.12, 42, 48)
            drums.note(t + beat + 0.25, 0.12, 42, 40)
            drums.note(t + beat + 0.75, 0.12, 42, 40)
        drums.note(t + 1, 0.25, 39, 92)                            # clap on 2 and 4
        drums.note(t + 3, 0.25, 39, 92)
        drums.note(t + 1, 0.25, 38, 60)
        drums.note(t + 3, 0.25, 38, 60)
        t += bpb
    drums.note(0, 1.0, 49, 90)                                     # crash at the start
    drums.note(INTRO_BARS * bpb, 1.0, 49, 96)
    for i, (a, b, name) in enumerate(spans):
        r = bass_note(name, 33)
        t = a
        while t < b - 1e-6:                                         # octave bass
            for k in range(8):
                bass.note(t + k * 0.5, 0.42, r + (12 if k % 2 else 0), 100 if k % 2 == 0 else 84)
            for off in (0.5, 1.75, 2.5, 3.5):                       # clav stabs
                for p in voicing(name, 60):
                    clav.note(t + off, 0.2, p, 70)
            t += bpb
        for p in voicing(name, 64):
            strings.note(a, b - a, p, 52)
        if i % 2 == 1:
            for p in voicing(name, 67):
                brass.note(a, 0.35, p, 82)
                brass.note(a + 0.5, 0.3, p, 72)
    return [drums, bass, clav, strings, brass], 81    # saw lead guide melody


STYLES = {"waltz": waltz, "sleigh": sleigh, "disco": disco}


def arrange(src: Path) -> tuple[bytes, dict]:
    from song import kar_words_track

    song = parse(src.read_text(encoding="utf-8"))
    bpb = song.beats_per_bar
    offset = INTRO_BARS * bpb
    end = offset + song.length_beats + bpb            # one bar to finish
    spans = chord_spans(song, offset, end)
    parts, guide_prog = STYLES[song.style](song, spans, bpb)

    melody = Part("Melody", 0, guide_prog)
    words = []
    first = True
    prev_end_line = True
    for n in song.notes:
        if n.midi is None:
            continue
        melody.note(n.start + offset, n.dur * 0.95, n.midi, 96)
        if n.syllable is None:
            continue
        txt = n.syllable
        words.append([int(round((n.start + offset) * TPB)), txt.rstrip(",.!?;:"), True, prev_end_line, first])
        first = False
        prev_end_line = txt[-1:] in ".!?"
    # word starts: a syllable follows a word end
    ends = [n for n in song.notes if n.midi is not None and n.syllable is not None]
    for i in range(1, len(words)):
        words[i][2] = ends[i - 1].word_end
    # lines: a sentence (. ! ?) ends a line; a long sentence is broken at its
    # commas, so a line holds at most MAX_LINE syllables where it can
    MAX_LINE = 12
    i = 0
    while i < len(words):
        j = i + 1
        while j < len(words) and not words[j][3]:
            j += 1
        commas = [k + 1 for k in range(i, j - 1) if ends[k].syllable.endswith(",")]
        start = i
        for c in commas:
            if c - start >= 4 and j - c >= 4 and j - start > MAX_LINE:
                words[c][3] = True
                start = c
        i = j
    mid = mido.MidiFile(type=1, ticks_per_beat=TPB)
    t0 = mido.MidiTrack()
    t0.append(mido.MetaMessage("track_name", name=song.title, time=0))
    t0.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(song.tempo_bpm), time=0))
    t0.append(mido.MetaMessage("time_signature", numerator=song.meter[0],
                               denominator=song.meter[1], time=0))
    t0.append(mido.MetaMessage("copyright", text=f"{song.composer}. Arrangement: Emkay songbook, CC0", time=0))
    t0.append(mido.MetaMessage("end_of_track", time=int(end * TPB)))
    mid.tracks.append(t0)
    mid.tracks.append(kar_words_track([tuple(w) for w in words], song.title,
                                      "ENGL" if song.language == "en" else song.language,
                                      song.composer))
    mid.tracks.append(melody.track())
    for p in parts:
        mid.tracks.append(p.track())
    import io
    buf = io.BytesIO()
    mid.save(file=buf)
    meta = {"title": song.title, "credits": song.composer, "language": song.language,
            "style": song.style}
    return buf.getvalue(), meta


def main():
    index = []
    for src in sorted((ROOT / "tools" / "songs").glob("*.abc")):
        data, meta = arrange(src)
        where = "songs" if src.stem in OFFLINE else "songbook"
        out = ROOT / where / f"{src.stem}.kar"
        out.write_bytes(data)
        if where == "songbook":
            index.append({"file": out.name, **meta})
        print(f"{out.relative_to(ROOT)}: {meta['title']} ({meta['style']})")
    (ROOT / "songbook" / "index.json").write_text(
        json.dumps({"songs": index}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
