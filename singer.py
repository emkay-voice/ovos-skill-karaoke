"""The singer: any TTS voice, autotuned onto the melody.

  1. Every word of the lyrics is spoken on its own by the device's own TTS
     (`speak(text) -> (samples, sample_rate)`). Spoken alone, a word's
     borders are known exactly - whatever the TTS - and it is said clearly,
     as a singer would. Words are cached, so a song only asks once per word.
  2. The word is split into its syllables in the sound itself (nuclei.py).
  3. Each syllable goes on its notes: the vowel starts on the beat, the
     consonants before it just ahead of the beat, the ones after it at the
     end of the note.
  4. TD-PSOLA (dsp.py) bends time and pitch onto the notes, with a glide
     between notes and a light vibrato on long ones.

Also gives the mouth movements (visemes) for the face: closed between
syllables, open or rounded on the vowels.

A song can also have lines that are said, not sung (song.spoken: "Okay...
but first we need some drums!"): the TTS says them as it always does, at
their time.
"""
from __future__ import annotations

import math
import re

import numpy as np

from .dsp import clean_f0, midi_hz, pitch_marks, psola, yin_f0
from .nuclei import find_syllables
from .song import Song, Syllable


def tts_text(word: str, voice_lang: str, song_lang: str) -> str:
    """A word as the TTS should get it. A voice reading another language's
    lyrics: no apostrophes (espeak reads them aloud) and lower case (so "It"
    isn't spelled I-T)."""
    w = re.sub(r"[^\w'’-]+", "", word)
    if voice_lang[:2] != (song_lang or voice_lang)[:2]:
        w = re.sub(r"['’]", "", w).lower()
    return w or word


def mouth(syllable: str) -> str:
    """The mouth shape of a syllable's vowel: rounded (o, u) or open."""
    for c in syllable.lower():
        if c in "ouyøåw":
            return "2"
        if c in "aeiæ":
            return "0"
    return "3"


def said_visemes(text: str, start: float, seconds: float):
    """The mouth for a spoken line: its words spread over the time it takes,
    closed between words, open or rounded on each ([code, until] pairs)."""
    words = [w for w in re.findall(r"[\w'’]+", text)]
    if not words or seconds <= 0:
        return []
    weights = [max(1, len(w)) for w in words]
    unit = seconds / sum(weights)
    out, t = [["4", round(start, 3)]], start
    for w, k in zip(words, weights):
        t += k * unit
        out.append([mouth(w), round(t - min(0.06, k * unit / 3), 3)])
        out.append(["4", round(t, 3)])
    return out


def choose_key(song: Song, voice_f0: float):
    """(transpose, octave): the band moves at most 6 semitones (more would put
    the bass in the cellar); the voice also moves whole octaves, so the melody
    sits a little above where the voice speaks."""
    pitches = [n.pitch for s in song.syllables for n in s.notes]
    if not pitches or voice_f0 <= 0:
        return 0, 0
    centre = float(np.median(pitches))
    target = 12 * math.log2(voice_f0 * 1.25 / 440.0) + 69
    shift = target - centre
    octave = round(shift / 12)
    transpose = int(round(shift - 12 * octave))
    return max(-6, min(6, transpose)), octave


def _nuclei_voiced(x: np.ndarray, sr: int, n: int) -> float:
    """How much of the least voiced vowel (of a word's `n` syllables) has a tone."""
    f0, hop, _ = yin_f0(x, sr)
    if not len(f0):
        return 1.0
    worst = 1.0
    for p in find_syllables(x, sr, n):
        a, b = p.nucleus[0] // hop, max(p.nucleus[0] // hop + 1, p.nucleus[1] // hop)
        seg = f0[a:b]
        if len(seg):
            worst = min(worst, float(np.mean(seg > 0)))
    return worst


class Singer:
    def __init__(self, speak, voice_lang: str = "en"):
        self.speak = speak                # text -> (float samples, sample rate)
        self.voice_lang = voice_lang
        self._words: dict[str, tuple[np.ndarray, int]] = {}
        self._sung: dict[tuple[str, int], tuple[np.ndarray, int]] = {}

    def word(self, text: str):
        if text not in self._words:
            x, sr = self.speak(text)
            self._words[text] = (np.asarray(x, dtype=np.float64), int(sr))
        return self._words[text]

    def sung_word(self, text: str, syllables: int):
        """`text` to sing, its `syllables` vowels with a tone. Said alone, a
        word ends a sentence, and some voices (Alan low, OpenVoiceOS'
        public default) breathe its last vowel out with hardly any tone:
        "Em-kay" was sung as "Em" and a whisper. Then the word is asked
        once more, as a question - asked, the voice keeps its tone to the
        end (the singer sets the pitch anyway) - and the better one sung."""
        key = (text, syllables)
        if key not in self._sung:
            x, sr = self.word(text)
            if _nuclei_voiced(x, sr, syllables) < 0.5:
                y, ysr = self.speak(f"{text}?")
                y = np.asarray(y, dtype=np.float64)
                if _nuclei_voiced(y, int(ysr), syllables) > _nuclei_voiced(x, sr, syllables):
                    x, sr = y, int(ysr)
            self._sung[key] = (x, sr)
        return self._sung[key]

    def voice_f0(self, words) -> float:
        f = []
        for w in words[:12]:
            x, sr = self.word(w)
            f0, _, _ = yin_f0(x, sr)
            f.extend(f0[f0 > 0])
        return float(np.median(f)) if f else 0.0

    def sing(self, song: Song, out_sr: int, transpose: int | None = None,
             octave: int | None = None, progress=None):
        """The vocal track (float samples at out_sr, aligned with the song's
        time 0), the visemes [[code, until_seconds]], and (transpose, octave)."""
        lines = song.lines
        words = [ws for line in lines for ws in song.words(line)]
        texts = [tts_text("".join(s.text for s in w), self.voice_lang, song.language) for w in words]
        for i, t in enumerate(dict.fromkeys(texts)):      # speak each word once
            self.word(t)
            if progress:
                progress(i + 1, len(set(texts)))
        if transpose is None or octave is None:
            transpose, octave = choose_key(song, self.voice_f0(texts))
        shift = transpose + 12 * octave

        sr = self.word(texts[0])[1] if texts else out_sr
        total = song.length + 2.0
        out = np.zeros(int(total * sr) + sr)
        wsum = np.zeros_like(out)

        sung = sorted({(n.start, n.end, n.pitch) for s in song.syllables for n in s.notes})
        starts = np.array([n[0] for n in sung]) if sung else np.zeros(1)
        ends = np.array([n[1] for n in sung]) if sung else np.zeros(1)
        hzs = np.array([midi_hz(n[2] + shift) for n in sung]) if sung else np.ones(1) * 200

        def f0_at(t, glide_s=0.045):
            i = int(np.clip(np.searchsorted(starts, t, side="right") - 1, 0, len(hzs) - 1))
            f = hzs[i]
            dt = t - starts[i]
            if i > 0 and dt < glide_s and starts[i] - ends[i - 1] < 0.05:
                f = hzs[i - 1] * (hzs[i] / hzs[i - 1]) ** (max(dt, 0) / glide_s)
            dur = ends[i] - starts[i]
            if dur > 1.0 and dt > 0.45:
                depth = min(1.0, (dt - 0.45) / 0.5) * 0.15
                f *= 2 ** (depth * np.sin(2 * np.pi * 5.0 * (dt - 0.45)) / 12)
            return f

        visemes = []
        for wi, (w, text) in enumerate(zip(words, texts)):
            sylls = [s for s in w if s.notes]
            if not sylls:
                continue
            x, wsr = self.sung_word(text, len(sylls))
            if wsr != sr:
                x = np.interp(np.arange(int(len(x) * sr / wsr)) * wsr / sr, np.arange(len(x)), x)
            f0, hop, flen = yin_f0(x, sr)
            f0 = clean_f0(f0)
            voiced_f0 = f0[f0 > 0]
            period = int(sr / np.median(voiced_f0)) if len(voiced_f0) else int(sr / 150)
            marks, periods, voiced = pitch_marks(x, sr, f0, hop, flen)
            parts = find_syllables(x, sr, len(sylls))
            nxt_word = next((ws for ws in words[wi + 1:] if any(s.notes for s in ws)), None)
            segs = self._layout(sylls, parts, sr, period, nxt_word)
            psola(x, marks, periods, voiced, segs, out, wsum, f0_at, sr)
            for s in sylls:                    # ovos_utils viseme codes, as [code, until]
                visemes.append(["4", round(s.notes[0].start, 3)])
                visemes.append([mouth(s.text), round(s.notes[-1].end - 0.05, 3)])
        vocal = out / np.maximum(wsum, 1.0)
        said, said_visemes = self.speak_lines(song, sr, len(vocal))
        vocal = vocal + said
        visemes = sorted(visemes + said_visemes, key=lambda v: v[1])
        if sr != out_sr:
            n = int(len(vocal) * out_sr / sr)
            vocal = np.interp(np.arange(n) * sr / out_sr, np.arange(len(vocal)), vocal)
        peak = np.abs(vocal).max()
        if peak > 0:
            vocal = vocal / peak
        return vocal, visemes, (transpose, octave)

    def speak_lines(self, song: Song, sr: int, n: int | None = None):
        """The song's spoken lines, each at its time, in the normal voice:
        (samples at sr, visemes). Each line about as loud as the singing."""
        n = n or int((song.length + 2.0) * sr)
        out = np.zeros(n)
        visemes = []
        for t, text in song.spoken:
            x, wsr = self.speak(text)
            x = np.asarray(x, dtype=np.float64)
            if wsr != sr:
                x = np.interp(np.arange(int(len(x) * sr / wsr)) * wsr / sr, np.arange(len(x)), x)
            x = x * (0.8 / (np.abs(x).max() or 1.0))
            a = int(t * sr)
            b = min(n, a + len(x))
            if b <= a:
                continue
            out[a:b] += x[: b - a]
            visemes += said_visemes(text, t, (b - a) / sr)
        return out, visemes

    @staticmethod
    def _layout(sylls: list[Syllable], parts, sr, period, nxt_word):
        """Sorted PSOLA segments (o0, o1, s0, s1, force_period) for one word."""
        segs = []
        for i, (s, p) in enumerate(zip(sylls, parts)):
            t0, t1 = s.notes[0].start, s.notes[-1].end
            on_len = min(p.onset[1] - p.onset[0], int(0.15 * sr))
            co_len = p.coda[1] - p.coda[0]
            # leave room for the next syllable's consonants before its beat
            if i + 1 < len(parts):
                nxt_on = parts[i + 1].onset[1] - parts[i + 1].onset[0]
                gap_to_next = sylls[i + 1].notes[0].start - t1
            else:
                nxt_on, gap_to_next = int(0.06 * sr), (nxt_word[0].time - t1 if nxt_word else 1)
            o_start = int(t0 * sr)
            o_end = int(t1 * sr) - (min(nxt_on, int(0.15 * sr)) if gap_to_next < 0.03 else 0)
            co_len = min(co_len, int(max(0, o_end - o_start) * 0.45))
            segs.append((o_start - on_len, o_start, p.onset[1] - on_len, p.onset[1], 0))
            segs.append((o_start, o_end - co_len, p.nucleus[0], p.nucleus[1], period))
            segs.append((o_end - co_len, o_end, p.coda[0], p.coda[0] + co_len, 0))
        return sorted(s for s in segs if s[1] > s[0])
