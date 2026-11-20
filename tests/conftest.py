"""Shared fixtures. The package is installed (pip install -e .), so the
modules are imported as ovos_skill_karaoke.*."""
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
VOWELS = "aeiouyæøå"


def fake_speak(text, sr=22050):
    """A stand-in TTS: per syllable a little consonant noise and a voiced
    vowel (a harmonic tone at 150 Hz), so the singer has real syllables to
    find without Piper. Syllables: vowel groups in the word."""
    rng = np.random.default_rng(len(text))
    groups, prev = 0, False
    for c in text.lower():
        v = c in VOWELS
        groups += v and not prev
        prev = v
    groups = max(1, groups)
    out = [np.zeros(int(0.05 * sr))]
    for _ in range(groups):
        out.append(0.05 * rng.standard_normal(int(0.05 * sr)))          # consonant
        t = np.arange(int(0.16 * sr)) / sr
        tone = sum(np.sin(2 * np.pi * 150 * k * t) / k for k in range(1, 8))
        out.append(0.3 * tone * np.hanning(len(t)) ** 0.3)
    out.append(np.zeros(int(0.05 * sr)))
    return np.concatenate(out), sr


@pytest.fixture
def speak():
    return fake_speak


@pytest.fixture
def kar():
    def read(name):
        for d in ("songs", "songbook"):
            p = ROOT / d / f"{name}.kar"
            if p.exists():
                return p.read_bytes()
        raise FileNotFoundError(name)
    return read
