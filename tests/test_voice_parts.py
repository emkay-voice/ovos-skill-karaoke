import numpy as np

from ovos_skill_karaoke.dsp import midi_hz, yin_f0
from ovos_skill_karaoke.nuclei import find_syllables
from ovos_skill_karaoke.singer import Singer, choose_key, mouth, tts_text
from ovos_skill_karaoke.song import load


def test_finds_the_syllables(speak):
    x, sr = speak("bicycle")             # three vowel groups: i, y, e
    sy = find_syllables(x, sr, 3)
    centres = [(s.nucleus[0] + s.nucleus[1]) / 2 / sr for s in sy]
    # vowels at 0.05 + k*0.21 + 0.05 .. +0.21
    for k, c in enumerate(centres):
        assert 0.1 + k * 0.21 - 0.03 < c < 0.26 + k * 0.21 + 0.03


def test_word_text_for_another_language():
    assert tts_text("won't", "da", "en") == "wont"
    assert tts_text("It", "da", "en") == "it"
    assert tts_text("It", "en", "en") == "It"


def test_key_suits_the_voice(kar):
    s = load(kar("daisy_bell"))
    t, o = choose_key(s, 110.0)               # a low voice
    assert -6 <= t <= 6 and o <= -1


def test_mouth():
    assert mouth("you") == "2" and mouth("Dai") == "0"


def test_sings_in_tune(kar, speak):
    s = load(kar("hey_emkay"))
    vocal, visemes, (t, o) = Singer(speak, "en").sing(s, 24000)
    shift = t + 12 * o
    f0, hop, _ = yin_f0(vocal, 24000)
    hits = total = 0
    for syl in s.syllables:
        for n in syl.notes:
            if n.end - n.start < 0.3:
                continue
            seg = f0[int((n.start + 0.08) * 24000 / hop): int((n.end - 0.08) * 24000 / hop)]
            seg = seg[seg > 0]
            total += 1
            if len(seg) and abs(12 * np.log2(np.median(seg) / midi_hz(n.pitch + shift))) < 0.5:
                hits += 1
    assert total > 10 and hits / total > 0.9
    assert visemes and all(visemes[i][1] <= visemes[i + 1][1] for i in range(len(visemes) - 1))
