import base64

import numpy as np

from ovos_skill_karaoke import band
from ovos_skill_karaoke.sheet import line_text, render
from ovos_skill_karaoke.song import load


def test_every_instrument_family_and_drum_sounds():
    for prog in range(0, 128, 4):
        y = band.instrument(prog, 60, 0.3, 100)
        assert np.isfinite(y).all() and np.abs(y).max() > 0.1
    for key in (35, 36, 37, 38, 39, 42, 46, 49, 51, 54, 83, 45, 70):
        y = band.drum(key, 100)
        assert np.isfinite(y).all() and np.abs(y).max() > 0.1


def test_renders_a_song(kar):
    s = load(kar("jingle_bells"))
    y = band.render_numpy(s.midi)
    assert abs(len(y) / band.SR - s.length) < 4
    assert 0.05 < np.sqrt((y ** 2).mean()) and np.abs(y).max() <= 1.0


def test_muted_melody_and_transpose(kar):
    s = load(kar("daisy_bell"))
    all_notes = band.notes(s.midi)
    without = band.notes(s.midi, {s.melody_track})
    assert len(without) < len(all_notes)
    up = band.notes(band.transposed(s.midi, 2))
    assert [n[4] for n in up if n[2] != 9][:5] == [n[4] + 2 for n in all_notes if n[2] != 9][:5]


def test_sheet(kar):
    s = load(kar("daisy_bell"))
    line = s.lines[0]
    svg = base64.b64decode(render(s.title, line, s.lines[1], s.chords, line[2].time).split(",")[1]).decode()
    assert "Dai" in svg and ">D<" in svg and "half crazy" in svg
    assert svg.count("<rect") > len(line)       # the melody's bars
    assert line_text(line) == "Daisy Daisy give me your answer do"
