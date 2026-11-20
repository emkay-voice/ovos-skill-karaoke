"""Find the syllables in spoken audio - from any TTS.

To sing, every syllable of the lyrics must be put on its note, so we need
to know where each syllable is in the TTS audio. Piper can tell (phoneme
alignments), but most TTS plugins only give a WAV - and a skill that should
work on every OVOS can't count on Piper. So the syllables are found in the
sound itself:

  * a syllable is a vowel, and a vowel is loud, voiced and has its energy
    in the 250-3000 Hz band, so a "vowel strength" curve has one hump per
    syllable;
  * the lyrics tell us how many syllables there are (n), so we keep the n
    strongest humps: neighbours separated by the shallowest dip are merged
    until n are left; too few, and the longest is split;
  * between two vowels the syllable boundary is the deepest point of the
    curve (a consonant or a pause).

Returns, per syllable, the onset (consonants before), the nucleus (the vowel)
and the coda (consonants after) as sample ranges.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .dsp import clean_f0, yin_f0


@dataclass
class Syl:
    onset: tuple[int, int]
    nucleus: tuple[int, int]
    coda: tuple[int, int]


def vowel_strength(x: np.ndarray, sr: int, hop_s: float = 0.005):
    """A curve (one value per hop) that has a hump on every vowel."""
    hop = max(1, int(hop_s * sr))
    win = int(0.025 * sr)
    n = max(1, 1 + (len(x) - win) // hop)
    pad = np.pad(x, (0, max(0, (n - 1) * hop + win - len(x))))
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    frames = pad[idx] * np.hanning(win)
    spec = np.abs(np.fft.rfft(frames, axis=1))
    freqs = np.fft.rfftfreq(win, 1 / sr)
    band = spec[:, (freqs >= 250) & (freqs <= 3000)]
    total = spec.sum(axis=1) + 1e-9
    e = np.sqrt((band ** 2).sum(axis=1))
    vowelish = band.sum(axis=1) / total            # fricatives sit higher
    f0, f0hop, flen = yin_f0(x, sr)
    f0 = clean_f0(f0)
    t_f0 = (np.arange(len(f0)) * f0hop + flen / 2) / sr
    t = (np.arange(n) * hop + win / 2) / sr
    voiced = np.interp(t, t_f0, (f0 > 0).astype(float)) if len(f0) else np.zeros(n)
    v = e * (0.15 + 0.85 * voiced) * np.clip(vowelish * 1.5, 0.2, 1.0)
    k = max(3, int(0.035 / hop_s)) | 1
    v = np.convolve(v, np.hanning(k) / np.hanning(k).sum(), mode="same")
    # in dB, as the ear hears it: weak unstressed syllables still make a hump
    db = 20 * np.log10(v / (v.max() + 1e-12) + 1e-6)
    return np.clip((db + 40) / 40, 0, 1), hop


def _peaks(v):
    return [i for i in range(1, len(v) - 1) if v[i] >= v[i - 1] and v[i] > v[i + 1]]


def find_syllables(x: np.ndarray, sr: int, n: int) -> list[Syl]:
    """Split spoken audio into n syllables."""
    if n <= 0 or len(x) == 0:
        return []
    v, hop = vowel_strength(x, sr)
    top = float(v.max()) or 1.0
    silent = v < 0.25 * top          # quieter than -30 dB
    peaks = [p for p in _peaks(v) if v[p] > 0.45 * top]   # louder than -22 dB
    if not peaks:
        peaks = [int(np.argmax(v))]

    def dip(i):           # how deep the valley between peaks i and i+1 is (1 = none)
        a, b = peaks[i], peaks[i + 1]
        return float(v[a:b + 1].min()) / max(1e-9, min(v[a], v[b]))

    while len(peaks) > n:  # merge the two humps with the shallowest dip
        i = max(range(len(peaks) - 1), key=dip)
        keep = peaks[i] if v[peaks[i]] >= v[peaks[i + 1]] else peaks[i + 1]
        peaks[i:i + 2] = [keep]

    # boundaries: deepest point between neighbours; outer ones at the edges
    # of the sound
    voiced_idx = np.where(~silent)[0]
    first = int(voiced_idx[0]) if len(voiced_idx) else 0
    last = int(voiced_idx[-1]) + 1 if len(voiced_idx) else len(v)
    while len(peaks) < n:  # split the widest syllable at its deepest inner dip
        bounds = [first] + [peaks[i] + int(np.argmin(v[peaks[i]:peaks[i + 1] + 1]))
                            for i in range(len(peaks) - 1)] + [last]
        i = int(np.argmax([bounds[k + 1] - bounds[k] for k in range(len(peaks))]))
        a, b = bounds[i], bounds[i + 1]
        if b - a < 8:                              # too short to split: give up
            break
        seg = v[a:b]
        inner = [k for k in range(2, len(seg) - 2) if seg[k] <= seg[k - 1] and seg[k] < seg[k + 1]]
        cut = a + (min(inner, key=lambda k: seg[k]) if inner else len(seg) // 2)
        left = a + int(np.argmax(v[a:cut])) if cut > a else a
        right = cut + int(np.argmax(v[cut:b])) if b > cut else cut
        peaks[i:i + 1] = [left, right] if left != right else [left]
        if left == right:
            break
    bounds = [first] + [peaks[i] + int(np.argmin(v[peaks[i]:peaks[i + 1] + 1]))
                        for i in range(len(peaks) - 1)] + [last]

    out = []
    for i, p in enumerate(peaks):
        a, b = bounds[i], bounds[i + 1]
        thr = v[p] - 0.15                          # within 6 dB of the peak
        s = p
        while s > a and v[s - 1] >= thr:
            s -= 1
        e = p + 1
        while e < b and v[e] >= thr:
            e += 1
        # consonants: trim silence (pauses between words) off their outer ends
        on_a = a
        while on_a < s and silent[on_a]:
            on_a += 1
        co_b = b
        while co_b > e and silent[co_b - 1]:
            co_b -= 1
        smp = lambda f: int(min(len(x), f * hop + int(0.0125 * sr)))
        out.append(Syl((smp(on_a), smp(s)), (smp(s), smp(e)), (smp(e), smp(co_b))))
    while len(out) < n:                              # very short audio: stretch the last
        out.append(Syl(out[-1].coda, out[-1].nucleus, out[-1].coda) if out else
                   Syl((0, 0), (0, len(x)), (len(x), len(x))))
    return out


def word_text(words: list[str]) -> str:
    """The text given to the TTS: the words with a comma between them, so any
    TTS pauses between words and their borders are easy to find (the pitch
    and timing of speech are thrown away anyway)."""
    return ", ".join(w.strip(" ,.;:!?\"'") or w for w in words)


def split_words(x: np.ndarray, sr: int, n_words: int):
    """Sample ranges of n_words words separated by pauses, or None if the
    pauses can't be told apart."""
    if n_words <= 1:
        nz = np.where(np.abs(x) > 1e-3)[0]
        return [(int(nz[0]), int(nz[-1]) + 1)] if len(nz) else None
    hop = int(0.005 * sr)
    k = max(1, len(x) // hop)
    rms = np.sqrt((x[: k * hop].reshape(k, hop) ** 2).mean(axis=1))
    rms = np.convolve(rms, np.ones(4) / 4, mode="same")
    quiet = rms < 0.04 * rms.max()
    gaps, i = [], 0                        # (length, start, end) of quiet runs
    while i < k:
        if quiet[i]:
            j = i
            while j < k and quiet[j]:
                j += 1
            gaps.append((j - i, i, j))
            i = j
        else:
            i += 1
    loud = np.where(~quiet)[0]
    if not len(loud):
        return None
    first, last = int(loud[0]), int(loud[-1]) + 1
    inner = [g for g in gaps if g[1] > first and g[2] < last and g[0] * 0.005 >= 0.03]
    if len(inner) < n_words - 1:
        return None
    inner = sorted(sorted(inner, reverse=True)[: n_words - 1], key=lambda g: g[1])
    # a stop consonant (p, t, k) also makes a short silence: the chosen
    # pauses must be clearly longer than any remaining one
    rest = [g for g in gaps if g not in inner and first < g[1] and g[2] < last]
    if rest and min(g[0] for g in inner) <= max(g[0] for g in rest):
        return None
    edges = [first] + [x for g in inner for x in (g[1], g[2])] + [last]
    return [(edges[2 * i] * hop, edges[2 * i + 1] * hop) for i in range(n_words)]


def find_word_syllables(x: np.ndarray, sr: int, counts: list[int]) -> list[Syl]:
    """Syllables of speech made from word_text(): split at the pauses into
    words, then each word into its syllables. Falls back to the whole line
    if the pauses can't be found."""
    words = split_words(x, sr, len(counts))
    if words is None:
        return find_syllables(x, sr, sum(counts))
    out = []
    for (a, b), n in zip(words, counts):
        pad = int(0.01 * sr)
        a0, b0 = max(0, a - pad), min(len(x), b + pad)
        for s in find_syllables(x[a0:b0], sr, n):
            out.append(Syl(*[(p + a0, q + a0) for p, q in (s.onset, s.nucleus, s.coda)]))
    return out
