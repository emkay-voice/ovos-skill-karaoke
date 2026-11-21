"""Gets a song ready to perform.

The band is ovos-phal-plugin-midi, the device's MIDI instrument: it is given
the song's file and plays it (built-in synth, FluidSynth, or a real
instrument), so nothing has to be rendered first.

  sing      Emkay sings: the band without the melody, in a key that suits
            the voice, and the voice (singer.py) as a clip the instrument
            plays in time with it. The voice is made once and kept (keyed by
            the song and the voice), with the key used and the mouth
            movements, which the face needs
  karaoke   you sing: the band with the melody as a guide, in the song's key;
            a song's spoken lines ("first we need some drums!") are still
            said by the device, as a clip
"""
from __future__ import annotations

import hashlib
import json
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .song import Song, load

VERSION = "4"   # bump when the voice changes, so old ones are made again
SR = 24000


@dataclass
class Performance:
    song: Song
    mode: str
    data: bytes                    # the song's file, for the band
    vocal: Path | None = None      # the sung voice (WAV), in sing mode
    transpose: int = 0             # the band's, in semitones
    visemes: list = field(default_factory=list)
    mute: list = field(default_factory=list)   # the melody's channels, while Emkay sings it


def melody_channels(song: Song) -> list:
    """The channels the melody track plays on."""
    if song.melody_track is None:
        return []
    return sorted({m.channel for m in song.midi.tracks[song.melody_track]
                   if m.type == "note_on" and m.channel != 9})


def _write_wav(path: Path, x: np.ndarray, sr: int):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


def _cached(data: bytes, mode: str, cache_dir: Path, voice_id: str):
    key = hashlib.sha1(data + f"|{mode}|{voice_id}|{VERSION}".encode()).hexdigest()[:16]
    return cache_dir / f"{key}.wav", cache_dir / f"{key}.json"


def ready(data: bytes, mode: str, cache_dir: Path, voice_id: str) -> bool:
    """Can it start at once: no voice to make first?"""
    path, meta = _cached(data, mode, cache_dir, voice_id)
    return mode != "sing" or (path.exists() and meta.exists())


def prepare(data: bytes, mode: str, cache_dir: Path, singer=None, voice_id: str = "",
            title: str = "", progress=None) -> Performance:
    song = load(data, title=title)
    if mode != "sing" and not (song.spoken and singer):
        return Performance(song, mode, data)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path, meta = _cached(data, mode, cache_dir, voice_id)
    if path.exists() and meta.exists():
        m = json.loads(meta.read_text())
    else:
        if mode == "sing":
            vocal, visemes, (transpose, _octave) = singer.sing(song, SR, progress=progress)
        else:                                   # only the spoken lines
            vocal, visemes = singer.speak_lines(song, SR)
            transpose = 0
        vocal = vocal * (0.8 / (np.abs(vocal).max() or 1.0))
        _write_wav(path, vocal, SR)
        m = {"transpose": transpose, "visemes": visemes}
        meta.write_text(json.dumps(m))
    mute = melody_channels(song) if mode == "sing" else []
    return Performance(song, mode, data, path, m["transpose"], m["visemes"], mute)
