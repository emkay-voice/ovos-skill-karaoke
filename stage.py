"""Gets a song ready to perform: one audio file, made once and then cached.

  sing      Emkay sings: the band without the melody track, in a key that
            suits the voice, plus the voice (singer.py)
  karaoke   you sing: the band with the melody as a guide, in the song's key

The file is kept (keyed by the song, the mode and the voice), so the second
time a song starts at once. Next to it: the key used and the mouth
movements, which the screen and the face need.
"""
from __future__ import annotations

import hashlib
import json
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import band
from .song import Song, load

VERSION = "1"   # bump when the sound changes, so old files are made again


@dataclass
class Performance:
    song: Song
    path: Path
    mode: str
    transpose: int
    visemes: list


def _write_wav(path: Path, x: np.ndarray, sr: int):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


def prepare(data: bytes, mode: str, cache_dir: Path, singer=None, voice_id: str = "",
            soundfont: str = "", title: str = "", progress=None) -> Performance:
    song = load(data, title=title)
    key = hashlib.sha1(data + f"|{mode}|{voice_id}|{VERSION}".encode()).hexdigest()[:16]
    cache_dir.mkdir(parents=True, exist_ok=True)
    path, meta = cache_dir / f"{key}.wav", cache_dir / f"{key}.json"
    if path.exists() and meta.exists():
        m = json.loads(meta.read_text())
        return Performance(song, path, mode, m["transpose"], m["visemes"])

    if mode == "sing":
        vocal, visemes, (transpose, _octave) = singer.sing(song, band.SR, progress=progress)
        skip = {song.melody_track} if song.melody_track is not None else set()
        backing = band.render(band.transposed(song.midi, transpose), skip, soundfont)
        n = max(len(backing), len(vocal))
        mix = np.zeros(n)
        mix[:len(backing)] += 0.5 * backing
        mix[:len(vocal)] += 0.8 * vocal
    else:
        transpose, visemes = 0, []
        mix = band.render(song.midi, (), soundfont)
    mix *= 0.9 / (np.abs(mix).max() or 1.0)
    _write_wav(path, mix, band.SR)
    meta.write_text(json.dumps({"transpose": transpose, "visemes": visemes}))
    return Performance(song, path, mode, transpose, visemes)
