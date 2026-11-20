import io

import mido

from ovos_skill_karaoke.song import kar_words_track, load


def test_shipped_songs_read(kar):
    for name in ("daisy_bell", "hey_emkay", "jingle_bells", "emkay_rock", "goodnight_emkay",
                 "offline_island", "hey_emkay.da", "hey_emkay.eu"):
        s = load(kar(name))
        assert s.title and s.language == (name.split(".")[1] if "." in name else "en")
        assert s.melody_track == 2                    # found, not told
        assert len(s.syllables) > 40
        assert all(syl.notes for syl in s.syllables)  # every syllable has its note


def test_words_and_lines(kar):
    s = load(kar("daisy_bell"))
    first = s.lines[0]
    assert [x.text for x in first[:2]] == ["Dai", "sy"]
    assert first[0].word_start and not first[1].word_start
    assert first[0].verse_start and first[0].line_start
    assert len(s.lines) == 5


def test_chords_found_from_the_band(kar):
    s = load(kar("jingle_bells"))
    assert [c.name for c in s.chords] == ["G", "C", "G", "A7", "D7", "G", "C", "G", "D7", "G"]
    s = load(kar("goodnight_emkay"))     # 6/8, a music box doubling the melody
    assert [c.name for c in s.chords][:4] == ["F", "C", "Bb", "F"]


def test_a_foreign_kar_file():
    """A .kar as other programs write it: lyrics with '/' and '\\', the melody
    on another track than ours, a tied note (melisma) and cp1252 text."""
    mid = mido.MidiFile(ticks_per_beat=96)
    t0 = mido.MidiTrack([mido.MetaMessage("set_tempo", tempo=600000, time=0)])
    band = mido.MidiTrack([mido.Message("program_change", program=0, channel=1)])
    for _ in range(8):
        for p in (48, 52, 55):
            band.append(mido.Message("note_on", note=p, velocity=60, channel=1, time=0))
        band.append(mido.Message("note_off", note=48, channel=1, time=96))
        band.append(mido.Message("note_off", note=52, channel=1, time=0))
        band.append(mido.Message("note_off", note=55, channel=1, time=0))
    melody = mido.MidiTrack()
    words = [(0, "\\Hej"), (96, " med"), (192, " dig,"), (288, "/Fø"), (384, "dsels"), (480, "dag"),
             (576, " i"), (672, " dag")]
    for i, p in enumerate([60, 62, 64, 65, 67, 69, 71, 72]):
        melody.append(mido.Message("note_on", note=p, velocity=90, time=0 if i == 0 else 0))
        melody.append(mido.Message("note_off", note=p, time=96))
    lyr = mido.MidiTrack([mido.MetaMessage("text", text="@TFødselsdag".encode("cp1252").decode("latin-1"))])
    last = 0
    for t, w in words:
        lyr.append(mido.MetaMessage("text", text=w.encode("cp1252").decode("latin-1"), time=t - last))
        last = t
    mid.tracks += [t0, lyr, band, melody]
    buf = io.BytesIO()
    mid.save(file=buf)
    s = load(buf.getvalue())
    assert s.title == "Fødselsdag"
    assert s.melody_track == 3
    assert [x.text for x in s.lines[1]] == ["Fø", "dsels", "dag", "i", "dag"]
    assert [x.word_start for x in s.lines[1]] == [True, False, False, True, True]
    assert s.syllables[0].notes[0].pitch == 60
    assert s.chords and s.chords[0].name == "C"


def test_words_track_round_trip():
    tr = kar_words_track([(0, "Dai", True, True, True), (10, "sy", False, False, False),
                          (20, "give", True, True, False)], "Daisy", "en")
    texts = [m.text for m in tr if m.type == "text"]
    assert texts[:4] == ["@KMIDI KARAOKE FILE", "@V0100", "@LEN", "@TDaisy"]
    assert texts[4:] == ["\\Dai", "sy", "/give"]
