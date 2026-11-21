# Development

## Setup
```bash
git clone https://github.com/emkay-voice/ovos-skill-karaoke.git
cd ovos-skill-karaoke
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"
```

## Running tests
```bash
pytest tests
```
(`tests/pytest.ini` keeps pytest from importing the repository root's
`__init__.py` as a plain module; the tests use the installed package.)

The tests need no TTS: `tests/conftest.py` has a stand-in voice that makes a
consonant and a voiced vowel per syllable, so the singer, the syllable finder
and the pitch can be checked without Piper. They check that every shipped
song reads with its melody track found and every syllable on a note, that
Jingle Bells' chords are recognised from the band, that a foreign-style .kar
(other markers, cp1252 text, melody on another track) reads, that the singer
lands within half a semitone on its notes, and the skill's sing / karaoke /
stop / cache flow, with what it sends the MIDI plugin (the band and the voice
at the same moment, the melody muted while it sings).

## The pieces

| file | |
|---|---|
| `song.py` | reads .kar / MIDI-with-lyrics: syllables, melody track, notes per syllable, chords |
| `nuclei.py` | finds the syllables in a spoken word (any TTS) |
| `singer.py` | puts the words on the notes: key, layout, visemes |
| `dsp.py` | YIN pitch, pitch marks, TD-PSOLA (numpy only) |
| `stage.py` | a song made ready: the sung voice (cached), the band's key and muted melody |
| `sheet.py` | the karaoke screen as SVG |
| `songbook.py` | own, shipped and online songs; matching titles |
| `voice.py` | the device's TTS: ovos-audio over the bus, or the plugin |
| `tools/arrange.py` | ABC sources -> .kar files |

Everything is numpy and pure Python (mido), so it also runs in Pyodide. The
band is [ovos-phal-plugin-midi](https://github.com/emkay-voice/ovos-phal-plugin-midi),
spoken to over the bus (`ovos.midi.play`, `ovos.midi.clip`, `ovos.midi.stop`).

## Adding a song to the songbook

Only songs that are free to use: in the public domain (composer *and*
lyricist dead for 70+ years, and the melody you write down taken from an
old printed source, not from someone's arrangement), or your own.

1. Write it as ABC in `tools/songs/<name>.abc`: melody, chord symbols
   (`"G"`, `"D7"`, `"Am"`) and the lyrics in `w:` lines, a syllable per
   note (`Dai-sy`), plus `%%language`, `%%tags` (for searching, any
   language) and `%%style` (`waltz`, `sleigh`, `disco`, `rocknroll`,
   `lullaby`, `reggae`, `hymn`, `round`, `nursery`, `swing`, `tango`; a new style is a function in `tools/arrange.py`).
   A song can also have:
   - `%%intro <bars>`: bars before the singing (default 2);
   - `%%enter drums 4, bass 9, band 14`: the band built up part by part
     (bars from the start; `swing` uses it);
   - `%%say <bar> <text>`: a line the device says with its normal voice at
     that bar ("first we need some drums!"). It is written as a cue marker
     `say:<text>`, which other karaoke players leave alone;
   - `%%lines w`: each `w:` line is a line on the screen (else lines end
     at a sentence, or at a comma once they are long);
   - the tag `coaxed`: a song that begins by giving in ("Okay... okay, I'll
     sing"), sung after "can you sing a song?" - "no" - "come on".
   Hyphenate as the word is spelled: the TTS gets the syllables joined
   (`køk-ke-net`, not `kø-ke-net`).
2. Other languages: `tools/songs/<name>.<lang>.lyrics` with a `T:` title,
   `%%language`, the `w:` lines - same melody, a syllable per note - and the
   `%%say <text>` lines in the same order (their bars are the song's).
3. `python tools/arrange.py` writes the .kar files: those in `OFFLINE` to
   `songs/` (shipped), the rest to `songbook/` with `index.json`. A line
   whose syllables don't match its notes stops it, naming the line.
4. Listen: `stage.prepare()` makes the voice, as the skill does, and
   ovos-phal-plugin-midi plays the file with it.

The build checks that the .kar files are what `tools/arrange.py` makes from
the sources, so commit both.

## Versioning

The version is `version` in `pyproject.toml`: `X.Y.Z`, or `X.Y.ZaN` for an
alpha. Pushing a tag `vX.Y.Z` builds, publishes to PyPI (trusted publishing)
and makes a GitHub release. Bump `stage.VERSION` when the sound changes, so
devices make their cached songs again.
