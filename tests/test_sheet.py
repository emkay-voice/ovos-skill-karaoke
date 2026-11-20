import base64

from ovos_skill_karaoke.sheet import line_text, render
from ovos_skill_karaoke.song import load
from ovos_skill_karaoke.stage import melody_channels


def test_sheet(kar):
    s = load(kar("daisy_bell"))
    line = s.lines[0]
    svg = base64.b64decode(render(s.title, line, s.lines[1], s.chords, line[2].time).split(",")[1]).decode()
    assert "Dai" in svg and ">D<" in svg and "half crazy" in svg
    assert svg.count("<rect") > len(line)       # the melody's bars
    assert line_text(line) == "Daisy Daisy give me your answer do"


def test_melody_channels(kar):
    s = load(kar("daisy_bell"))
    chans = melody_channels(s)
    assert chans and 9 not in chans
