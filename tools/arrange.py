"""Turn the songbook's ABC sources into MIDI-karaoke (.kar) files.

    python tools/arrange.py            # all of tools/songs/*.abc

A source holds the melody, the chords and the lyrics; the arrangement (band,
groove) comes from its %%style. The result is an ordinary .kar file - any
karaoke player can play it - with General MIDI instruments:

  track 0  tempo, meter, title
  track 1  Words: the lyrics, Soft Karaoke style
  track 2  Melody (the guide the karaoke mode plays; muted when Emkay sings)
  track 3  Chords: the chord symbols as marker events (for the screen)
  track 4+ the band

Songs listed in OFFLINE are written to songs/ (shipped with the skill), the
rest to songbook/ (fetched when asked for).

Other languages: tools/songs/<name>.<lang>.lyrics holds a title (T:) and the
w: lines for the same melody - a singable rewrite, a syllable per note. It
becomes <name>.<lang>.kar next to the song. A lyric that doesn't fit the
notes stops the build, with the line that is off.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import mido

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT))

from abcnotation import parse  # noqa: E402

TPB = 480
OFFLINE = {"daisy_bell", "hey_emkay", "goodnight_emkay", "jerusalem", "frere_jacques", "emkay_swing",
           "happy_birthday", "twinkle"}
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


def _swing(t):
    """Shuffle: an off-beat eighth comes a triplet later."""
    frac = t % 1
    return t + 1 / 6 if abs(frac - 0.5) < 1e-6 else t


def rocknroll(song, spans, bpb):
    drums, bass, piano, guitar = (Part("Drums", 9, 0), Part("Bass", 1, 33),
                                  Part("Piano", 2, 0), Part("Guitar", 3, 29))
    end = spans[-1][1]
    t = 0
    while t < end - 1e-6:
        for beat in range(4):
            drums.note(t + beat, 0.2, 42, 70)
            drums.note(t + beat + 0.5, 0.2, 42, 52)
        drums.note(t, 0.25, 36, 100)
        drums.note(t + 2, 0.25, 36, 92)
        drums.note(t + 2.5, 0.25, 36, 70)
        drums.note(t + 1, 0.25, 38, 96)
        drums.note(t + 3, 0.25, 38, 96)
        t += bpb
    drums.note(0, 1.0, 49, 90)
    drums.note(INTRO_BARS * bpb, 1.0, 49, 100)
    for a, b, name in spans:
        r = bass_note(name, 36)
        boogie = [r, r + 4, r + 7, r + 9, r + 12, r + 9, r + 7, r + 4]
        t = a
        while t < b - 1e-6:
            for k in range(8):                    # boogie-woogie bass
                bass.note(t + k * 0.5, 0.4, boogie[k], 96 if k % 2 == 0 else 80)
            for off in (0.5, 1.5, 2.5, 3.5):      # piano on the off-beats
                for p in voicing(name, 60):
                    piano.note(t + off, 0.3, p, 64)
            for off in (1, 3):                    # guitar chops on 2 and 4
                for p in voicing(name, 52):
                    guitar.note(t + off, 0.35, p, 70)
            t += bpb
    return [drums, bass, piano, guitar], 66       # tenor sax guide melody


def lullaby(song, spans, bpb):
    """6/8: a harp rocking in dotted quarters, soft strings, a music box."""
    harp, strings, box = Part("Harp", 1, 46), Part("Strings", 2, 49), Part("Music box", 3, 10)
    for a, b, name in spans:
        tones = voicing(name, 53)
        arp = [bass_note(name, 41)] + tones + [tones[1] + 12, tones[0] + 12]
        t = a
        k = 0
        while t < b - 1e-6:                       # eighths: up and down the chord
            harp.note(t, 1.2, arp[[0, 1, 2, 3, 2, 1][k % 6]], 58 if k % 3 == 0 else 44)
            t += 0.5
            k += 1
        for p in voicing(name, 60):
            strings.note(a, b - a, p, 34)
    for n in song.notes:
        if n.midi is not None:
            box.note(n.start + INTRO_BARS * bpb, min(n.dur, 1.5), n.midi + 12, 30)
    return [harp, strings, box], 79               # ocarina guide melody


def reggae(song, spans, bpb):
    """One drop: kick and rim on 3, the skank on 2 and 4, a bubbling organ."""
    drums, bass, guitar, organ = (Part("Drums", 9, 0), Part("Bass", 1, 33),
                                  Part("Guitar", 2, 27), Part("Organ", 3, 16))
    end = spans[-1][1]
    t = 0
    while t < end - 1e-6:
        drums.note(t + 2, 0.25, 36, 100)
        drums.note(t + 2, 0.25, 37, 84)
        for k in range(8):
            drums.note(t + k * 0.5, 0.15, 42, 58 if k % 2 else 44)
        t += bpb
    for a, b, name in spans:
        r = bass_note(name, 36)
        t = a
        while t < b - 1e-6:
            for off, p, d in ((0, r, 0.9), (1.5, r + 7, 0.4), (2.0, r + 7, 0.4),
                              (2.5, r + 12, 0.4), (3.5, r + 4, 0.4)):
                if t + off < b - 1e-6:
                    bass.note(t + off, d, p, 100)
            for off in (1, 3):                    # the skank
                for p in voicing(name, 60):
                    guitar.note(t + off, 0.18, p, 76)
            for off in (0.5, 1.5, 2.5, 3.5):      # organ bubble
                for p in voicing(name, 55):
                    organ.note(t + off, 0.22, p, 46)
            t += bpb
    return [drums, bass, guitar, organ], 22       # harmonica guide melody


def hymn(song, spans, bpb):
    """Organ, strings and a choir holding the chords, the pedal on the root."""
    organ, pedal, strings, choir = (Part("Organ", 1, 19), Part("Pedal", 2, 19),
                                    Part("Strings", 3, 48), Part("Choir", 4, 52))
    for a, b, name in spans:
        pedal.note(a, b - a, bass_note(name, 36), 80)
        for p in voicing(name, 55):
            organ.note(a, b - a, p, 62)
        for p in voicing(name, 62):
            strings.note(a, b - a, p, 40)
        for p in voicing(name, 50):
            choir.note(a, b - a, p, 34)
    return [organ, pedal, strings, choir], 56     # trumpet guide melody


def round_(song, spans, bpb):
    """A round: the band, and a flute starting the melody again two bars later."""
    piano, bass, flute, glock = (Part("Piano", 1, 0), Part("Bass", 2, 32),
                                 Part("Canon", 3, 73), Part("Glockenspiel", 4, 9))
    last = spans[-1]
    for a, b, name in spans + [(last[1], last[1] + bpb, last[2])]:
        t = a
        while t < b - 1e-6:
            bass.note(t, 0.9, bass_note(name, 41), 80)
            bass.note(t + bpb / 2, 0.9, bass_note(name, 41) + 7, 70)
            for p in voicing(name, 57):
                piano.note(t, bpb / 2 - 0.1, p, 52)
                piano.note(t + bpb / 2, bpb / 2 - 0.1, p, 46)
            t += bpb
    for n in song.notes:
        if n.midi is not None:
            flute.note(n.start + (INTRO_BARS + 2) * bpb, n.dur * 0.95, n.midi + 12, 62)
            if n.syllable and n.syllable.lower().strip(",.!?") in ("ding", "dang", "dong", "bim", "bam",
                                                                    "bum", "din", "don", "dan", "dão",
                                                                    "bom", "daing"):
                glock.note(n.start + INTRO_BARS * bpb, 1.0, n.midi + 24, 70)
    return [piano, bass, flute, glock], 11        # vibraphone guide melody


def nursery(song, spans, bpb):
    """Celesta rocking through the chord, pizzicato bass, a soft string pad."""
    celesta, bass, strings, glock = (Part("Celesta", 1, 8), Part("Bass", 2, 45),
                                     Part("Strings", 3, 48), Part("Glockenspiel", 4, 9))
    for a, b, name in spans:
        tones = voicing(name, 60)
        arp = [tones[0], tones[1], tones[2], tones[1]]
        t, k = a, 0
        while t < b - 1e-6:
            celesta.note(t, 0.9, arp[k % 4], 50 if k % 2 == 0 else 40)
            if k % 2 == 0:
                bass.note(t, 0.6, bass_note(name, 41) + (7 if k % 4 == 2 else 0), 72)
            t += 1
            k += 1
        for p in voicing(name, 55):
            strings.note(a, b - a, p, 30)
    for n in song.notes:
        if n.midi is not None:
            glock.note(n.start + INTRO_BARS * bpb, min(n.dur, 1.0), n.midi + 12, 26)
    return [celesta, bass, strings, glock], 73    # flute guide melody


def _walk(name, nxt, low=36, odd=False):
    """A walking bass bar: root, third, fifth (or up the scale, every other
    bar), and a half step into the next chord."""
    r = bass_note(name, low)
    q = name[2:] if len(name) > 1 and name[1] in "#b" else name[1:]
    third = r + (3 if q.startswith("m") and not q.startswith("maj") else 4)
    n = bass_note(nxt, low)
    approach = n - 1 if n - 1 != r + 7 else n + 1
    return [r, r + 2, third, approach] if odd else [r, third, r + 7, approach]


def swing(song, spans, bpb):
    """A big band, swinging: ride cymbal, walking bass, Charleston piano,
    saxes holding the chords, trumpets and trombones punching. With %%enter
    it is built up part by part - "first we need some drums!" - drums, then
    the bass, then BAM, the whole band (bars from the start of the song)."""
    drums, bass, piano = Part("Drums", 9, 0), Part("Bass", 1, 32), Part("Piano", 2, 0)
    saxes, trumpets, bones = Part("Saxes", 3, 65), Part("Trumpets", 4, 56), Part("Trombones", 5, 57)
    enter = getattr(song, "enter", {})
    d0 = enter.get("drums", 0) * bpb
    b0 = enter.get("bass", 0) * bpb
    band = enter.get("band", 0) * bpb
    melody_at = getattr(song, "intro_bars", INTRO_BARS) * bpb
    end = spans[-1][1]
    final = end - bpb                              # the last bar: one big chord
    t, bar = d0, 0
    while t < final - 1e-6:
        first_band_bar = abs(t - band) < 1e-6 and band > 0
        for beat in range(4):                      # ride: ding, ding-ga-ding
            drums.note(t + beat, 0.3, 51, 112 if beat % 2 else 100)
            # the kick: feathered on every beat; on 1 and 3 before the band is in
            drums.note(t + beat, 0.2, 36, 100 if t < band and beat % 2 == 0 else 62)
        for off in (1.5, 3.5):                     # the skip notes (swung later)
            drums.note(t + off, 0.2, 51, 88)
        drums.note(t + 1, 0.2, 44, 96)             # hi-hat foot on 2 and 4
        drums.note(t + 3, 0.2, 44, 96)
        drums.note(t + 1, 0.2, 37, 70)             # and a cross-stick with it
        drums.note(t + 3, 0.2, 37, 78)
        if bar % 2 == 1:
            drums.note(t + 2.5, 0.15, 38, 58)      # snare comps
            drums.note(t + 3.5, 0.15, 38, 66)
        next_is_entry = any(abs(t + bpb - x) < 1e-6 for x in (b0, band, melody_at)) and t + bpb > d0
        if next_is_entry or (t >= band and bar % 8 == 7):
            for k, off in enumerate((2, 2.5, 3, 3.5)):   # a fill into what comes
                drums.note(t + off, 0.2, 38, 70 + 8 * k)
            drums.note(t + 3.5, 0.2, 45, 80)
        if first_band_bar:
            drums.note(t, 1.5, 49, 120)            # BAM
            drums.note(t, 0.3, 36, 115)
        t += bpb
        bar += 1
    drums.note(final, 3.0, 49, 120)
    drums.note(final, 0.3, 36, 120)
    for k in range(12):                            # a roll into the last hit
        drums.note(final - 1 + k / 12, 0.08, 38, 60 + 4 * k)

    for i, (a, b, name) in enumerate(spans):
        nxt = spans[i + 1][2] if i + 1 < len(spans) else name
        t = a
        while t < b - 1e-6 and t < final - 1e-6:
            if t >= b0:
                walk = _walk(name, nxt if t + bpb >= b - 1e-6 else name, odd=int(round(t / bpb)) % 2 == 1)
                for k in range(4):
                    if t + k < b - 1e-6:
                        bass.note(t + k, 0.85, walk[k], 92 if k % 2 == 0 else 82)
            if t >= band:
                v = voicing(name, 58)
                for off, d in ((0, 0.4), (1.5, 0.35)):     # Charleston
                    if t + off < b - 1e-6:
                        for p in v:
                            piano.note(t + off, d, p, 64)
                for p in voicing(name, 62):
                    saxes.note(t, min(bpb, b - t) * 0.95, p, 46)
            t += bpb
    # the band's punches: a hit on the "and" of 4 every other bar, answering
    # the singer; in the intro a shout riff; at the end the last chord
    for i, (a, b, name) in enumerate(spans):
        t = a
        while t < b - 1e-6 and t < final - 1e-6:
            if t >= band and t < melody_at:
                if abs(t - band) < 1e-6:                    # BAM: the whole band
                    for p in voicing(name, 65) + [voicing(name, 65)[0] + 12]:
                        trumpets.note(t, 1.2, p, 112)
                    for p in voicing(name, 50):
                        bones.note(t, 1.2, p, 104)
                riff = [(1.5, 0), (2.0, 4), (2.5, 7), (3.5, 9)]
                root = voicing(name, 65)[0]
                for off, iv in riff:
                    trumpets.note(t + off, 0.4, root + iv, 96)
                    bones.note(t + off, 0.4, root + iv - 12, 84)
            elif t >= melody_at:
                bar_no = int(round((t - melody_at) / bpb))
                if bar_no % 2 == 1:
                    for p in voicing(name, 65):
                        trumpets.note(t + 3.5, 0.3, p, 84)
                    for p in voicing(name, 50):
                        bones.note(t + 3.5, 0.3, p, 76)
            t += bpb
    last = spans[-1][2]
    for p in voicing(last, 65) + [voicing(last, 65)[0] + 12]:
        trumpets.note(final, 3.0, p, 110)
    for p in voicing(last, 50):
        bones.note(final, 3.0, p, 100)
    for p in voicing(last, 58):
        saxes.note(final, 3.0, p, 80)
        piano.note(final, 3.0, p, 80)
    bass.note(final, 3.0, bass_note(last, 29), 100)
    return [drums, bass, piano, saxes, trumpets, bones], 59    # muted trumpet guide melody


STYLES = {"waltz": waltz, "sleigh": sleigh, "disco": disco,
          "rocknroll": rocknroll, "lullaby": lullaby, "reggae": reggae, "hymn": hymn,
          "round": round_, "nursery": nursery, "swing": swing}
SWING = {"rocknroll", "swing"}


def directives(text: str):
    """The arrangement's own lines: %%intro <bars> (before the singing,
    default 2), %%enter <part> <bar>, ... (when the band's parts come in, in
    bars from the start), %%say <bar> <text> (a line the device says, not
    sings, at that bar)."""
    intro, enter, say = INTRO_BARS, {}, []
    for ln in text.splitlines():
        if ln.startswith("%%intro"):
            intro = int(ln.split()[1])
        elif ln.startswith("%%enter"):
            for item in ln[len("%%enter"):].split(","):
                part, bar = item.split()
                enter[part] = float(bar)
        elif ln.startswith("%%say"):
            _, bar, words = ln.split(None, 2)
            say.append((float(bar), words.strip()))
    return intro, enter, say


def with_lyrics(abc: str, lyrics: str) -> str:
    """The ABC source with its title, language, w: lines and %%say lines
    from a .lyrics file (its %%say lines replace the song's, in order, at the
    same bars: "%%say <text>")."""
    lang = None
    title, words, says = None, [], []
    for ln in lyrics.splitlines():
        if ln.startswith("T:"):
            title = ln
        elif ln.startswith("%%language"):
            lang = ln
        elif ln.startswith("w:"):
            words.append(ln)
        elif ln.startswith("%%say"):
            says.append(ln[len("%%say"):].strip())
    out, wi, si = [], 0, 0
    for ln in abc.splitlines():
        if ln.startswith("%%say") and says:
            if si >= len(says):
                raise ValueError("fewer %%say lines than the song has")
            ln = f"%%say {ln.split()[1]} {says[si]}"
            si += 1
        elif ln.startswith("w:"):
            if wi >= len(words):
                raise ValueError("fewer w: lines than the song has")
            ln = words[wi]
            wi += 1
        elif ln.startswith("T:") and title:
            ln, title = title, ""
        elif ln.startswith("%%language") and lang:
            ln = lang
        out.append(ln)
    if wi != len(words):
        raise ValueError("more w: lines than the song has")
    if says and si != len(says):
        raise ValueError("more %%say lines than the song has")
    return "\n".join(out)


def check_fit(text: str, name: str):
    """Each w: line must have exactly a syllable per note of the music line
    above it (a tied note is one note)."""
    from abcnotation import _lyric_tokens
    music = None
    for ln in text.splitlines():
        if ln.startswith("|"):
            music = re.sub(r'"[^"]*"', "", ln)         # chord symbols out
        elif ln.startswith("w:") and music:
            notes = len(re.findall(r"[A-Ga-g]", music)) - music.count("-")
            syllables = len(_lyric_tokens(ln[2:]))
            if syllables != notes:
                raise ValueError(f"{name}: {syllables} syllables for {notes} notes: {ln[2:].strip()}")


def arrange(src: Path, lyrics: Path | None = None) -> tuple[bytes, dict]:
    from song import kar_words_track

    text = src.read_text(encoding="utf-8")
    if lyrics:
        text = with_lyrics(text, lyrics.read_text(encoding="utf-8"))
    check_fit(text, (lyrics or src).name)
    song = parse(text)
    bpb = song.beats_per_bar
    song.intro_bars, song.enter, says = directives(text)
    offset = song.intro_bars * bpb
    end = offset + song.length_beats + bpb            # one bar to finish
    spans = chord_spans(song, offset, end)
    parts, guide_prog = STYLES[song.style](song, spans, bpb)

    if song.style in SWING:
        for p in parts:
            p.ev = [(int(round(_swing(t / TPB) * TPB)), on, pi, v) for t, on, pi, v in p.ev]
    sw = _swing if song.style in SWING else (lambda t: t)
    melody = Part("Melody", 0, guide_prog)
    words = []
    first = True
    prev_end_line = True
    for n in song.notes:
        if n.midi is None:
            continue
        st, en = sw(n.start + offset), sw(n.start + offset + n.dur)
        melody.note(st, (en - st) * 0.95, n.midi, 96)
        if n.syllable is None:
            continue
        txt = n.syllable
        words.append([int(round(sw(n.start + offset) * TPB)), txt.rstrip(",.!?;:"), True, prev_end_line, first])
        first = False
        prev_end_line = txt[-1:] in ".!?"
    # word starts: a syllable follows a word end
    ends = [n for n in song.notes if n.midi is not None and n.syllable is not None]
    for i in range(1, len(words)):
        words[i][2] = ends[i - 1].word_end
    # lines: a sentence (. ! ?) ends a line; inside one, a comma does once
    # the line has 6 syllables and at least 4 are left for the next
    i = 0
    while i < len(words):
        j = i + 1
        while j < len(words) and not words[j][3]:
            j += 1
        start = i
        for k in range(i, j - 1):
            c = k + 1
            if ends[k].syllable.endswith(",") and c - start >= 6 and j - c >= 4:
                words[c][3] = True
                start = c
        # still long (a poem without commas): break before a capital that
        # starts a verse line, the one giving a line nearest 8 syllables
        # (hymn metre) - "Holy" and "God" are capitals too; no capital: a word
        start = max([c for c in range(i, j) if words[c][3]] or [i])
        while j - start > 12:
            span = range(start + 6, min(j - 4, start + 12) + 1)
            cands = [c for c in span
                     if words[c][2] and words[c][1][:1].isupper() and words[c][1] != "I"]
            cands = cands or [c for c in span if words[c][2]]     # else any word
            if not cands:
                break
            c = min(cands, key=lambda c: abs(c - start - 8))
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
                                      "ENGL" if song.language == "en" else song.language.upper(),
                                      song.composer, song.tags))
    mid.tracks.append(melody.track())
    chords = mido.MidiTrack([mido.MetaMessage("track_name", name="Chords", time=0)])
    last = 0
    for a, _, name in spans:                     # the chords as markers, for the screen
        tick = int(round(sw(a) * TPB))
        chords.append(mido.MetaMessage("marker", text=name, time=tick - last))
        last = tick
    chords.append(mido.MetaMessage("end_of_track", time=0))
    mid.tracks.append(chords)
    if says:
        from song import spoken_track
        mid.tracks.append(spoken_track([(int(round(bar * bpb * TPB)), words) for bar, words in says]))
    for p in parts:
        mid.tracks.append(p.track())
    import io
    buf = io.BytesIO()
    mid.save(file=buf)
    joined = []
    for _, text, word_start, _, _ in words:       # syllables -> words, for searching
        if word_start or not joined:
            joined.append("")
        joined[-1] += text
    lyric_words = sorted({re.sub(r"[^\w']", "", w).lower() for w in joined} - {""})
    meta = {"title": song.title, "credits": song.composer, "language": song.language,
            "style": song.style, "tags": song.tags, "words": lyric_words}
    return buf.getvalue(), meta


def main():
    index = []
    for src in sorted((ROOT / "tools" / "songs").glob("*.abc")):
        variants = [(None, src.stem)] + [
            (ly, ly.name[: -len(".lyrics")]) for ly in sorted(src.parent.glob(f"{src.stem}.*.lyrics"))]
        for lyrics, name in variants:
            data, meta = arrange(src, lyrics)
            where = "songs" if src.stem in OFFLINE else "songbook"
            out = ROOT / where / f"{name}.kar"
            out.write_bytes(data)
            if where == "songbook":
                index.append({"file": out.name, **meta})
            print(f"{out.relative_to(ROOT)}: {meta['title']} ({meta['language']}, {meta['style']})")
    (ROOT / "songbook" / "index.json").write_text(
        json.dumps({"songs": index}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
