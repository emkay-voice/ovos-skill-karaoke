"""Signal processing for the singing voice: pitch tracking, pitch marks and
TD-PSOLA. numpy only, so it runs everywhere OVOS runs - Pyodide included.
"""
from __future__ import annotations

import numpy as np


def yin_f0(x: np.ndarray, sr: int, hop: int = 128, fmin=65.0, fmax=450.0):
    """Frame-wise F0 (Hz, 0 = unvoiced) with YIN. All frames at once: the
    difference function from FFT cross-correlations (fast, also in Pyodide)."""
    w = int(sr / fmin) * 2
    half = w // 2
    tmin, tmax = int(sr / fmax), int(sr / fmin)
    n = max(1, (len(x) - w) // hop)
    if len(x) < w:
        return np.zeros(n), hop, w
    idx = np.arange(w)[None, :] + hop * np.arange(n)[:, None]
    fr = x[idx]
    loud = np.sqrt(np.mean(fr ** 2, axis=1)) >= 0.01
    size = 1 << int(np.ceil(np.log2(w + half)))
    r = np.fft.irfft(np.fft.rfft(fr, size, axis=1) * np.conj(np.fft.rfft(fr[:, :half], size, axis=1)),
                     size, axis=1)[:, : tmax + 1]
    cs = np.concatenate([np.zeros((n, 1)), np.cumsum(fr ** 2, axis=1)], axis=1)
    taus = np.arange(tmax + 1)
    e0 = cs[:, half][:, None]
    e_tau = cs[:, taus + half] - cs[:, taus]
    d = np.maximum(e0 + e_tau - 2 * r, 0.0)
    cmnd = np.ones_like(d)
    cmnd[:, 1:] = d[:, 1:] * taus[1:] / np.maximum(np.cumsum(d[:, 1:], axis=1), 1e-9)
    f0 = np.zeros(n)
    below = cmnd[:, tmin:tmax] < 0.15
    for i in np.where(loud & below.any(axis=1))[0]:
        tau = tmin + int(np.argmax(below[i]))
        while tau + 1 < tmax and cmnd[i, tau + 1] < cmnd[i, tau]:
            tau += 1
        f0[i] = sr / tau
    return f0, hop, w


def clean_f0(f0: np.ndarray, max_gap: int = 8):
    """Fix octave jumps, median-smooth and bridge short unvoiced gaps, so
    vowels keep pitch-synchronous marks all the way through."""
    f = f0.copy()
    v = f > 0
    if v.sum() < 3:
        return f
    med = np.median(f[v])
    f[v & (f > med * 1.7)] /= 2           # octave errors
    f[v & (f < med / 1.7)] *= 2
    g = f.copy()                           # 5-point median over voiced frames
    for i in np.where(v)[0]:
        w = f[max(0, i - 2): i + 3]
        w = w[w > 0]
        g[i] = np.median(w)
    idx = np.where(v)[0]                   # bridge short gaps
    for a, b in zip(idx[:-1], idx[1:]):
        if 1 < b - a <= max_gap:
            g[a + 1:b] = np.linspace(g[a], g[b], b - a + 1)[1:-1]
    return g


def pitch_marks(x: np.ndarray, sr: int, f0: np.ndarray, hop: int, frame_len: int):
    """Epoch marks: pitch-synchronous in voiced parts (always the positive
    peak of a smoothed signal, so the phase stays consistent), every 5 ms
    elsewhere. Returns (marks, period_at_mark, voiced_at_mark)."""
    k = max(1, int(sr / 2500))
    xs = np.convolve(x, np.ones(k) / k, mode="same")
    centre = frame_len // 2
    marks, periods, voiced = [], [], []
    t = 0
    uv_step = int(0.005 * sr)
    last_voiced = False
    while t < len(x):
        fi = int(np.clip((t - centre) // hop, 0, len(f0) - 1))
        f = f0[fi]
        if f > 0:
            p = int(round(sr / f))
            r = p // 2 if not last_voiced else p // 5
            lo, hi = max(0, t - r), min(len(x), t + r + 1)
            if hi > lo:
                t = lo + int(np.argmax(xs[lo:hi]))
            marks.append(t); periods.append(p); voiced.append(True)
            t += p
            last_voiced = True
        else:
            marks.append(t); periods.append(uv_step); voiced.append(False)
            t += uv_step
            last_voiced = False
    return np.array(marks), np.array(periods), np.array(voiced)


# ---------------------------------------------------------------- syllables

def _steady_region(x, marks, periods, voiced, s0, s1):
    """Longest run of voiced, reasonably loud marks in [s0, s1] whose period
    stays within 6% of its neighbour - skips stød/creak, transitions and the
    fade-out at the end of a sentence."""
    idx = np.where((marks >= s0) & (marks <= s1))[0]
    if len(idx) == 0:
        return None
    rms = np.array([np.sqrt(np.mean(x[max(0, marks[k] - periods[k]): marks[k] + periods[k]] ** 2))
                    for k in idx])
    loud = dict(zip(idx, rms >= 0.5 * rms.max()))
    best, cur = (None, 0), []
    for k in idx:
        ok = voiced[k] and loud[k] and (not cur or abs(periods[k] - periods[cur[-1]]) <= 0.06 * periods[k])
        cur = cur + [k] if ok else ([k] if voiced[k] and loud[k] else [])
        if len(cur) > best[1]:
            best = ((marks[cur[0]], marks[cur[-1]]), len(cur))
    if best[0] is None or best[1] < 4:
        return None
    return best[0]


def _src_pos(t, o0, o1, s0, s1, is_vowel, region=None, speed=0.45, sr_guess=22050):
    """Map an output sample to a source position. Consonants and vowels that
    aren't stretched much map linearly. A strongly stretched vowel enters,
    then ping-pongs through its middle 60% at `speed` x real time, then exits."""
    olen, slen = o1 - o0, s1 - s0
    if not is_vowel or slen <= 0 or olen <= slen / speed:
        return s0 + (t - o0) * slen / max(1, olen)
    if region is None:
        return s0 + (t - o0) * slen / max(1, olen)
    a, b = region
    enter = (a - s0) / speed                  # output samples to reach a
    leave = (s1 - b) / speed
    u = t - o0
    if u < enter:
        return s0 + u * speed
    if u > olen - leave:
        return b + (u - (olen - leave)) * speed
    # drift slowly and irregularly inside [a, b] (a fixed back-and-forth
    # rhythm is heard as an echo); start at a, end at b
    mid = olen - enter - leave
    tt = (u - enter) / sr_guess
    span = b - a
    wob = (np.sin(2 * np.pi * 0.61 * tt) + 0.6 * np.sin(2 * np.pi * 1.37 * tt + 1.1)
           + 0.4 * np.sin(2 * np.pi * 2.23 * tt + 2.3)) / 2.0          # in [-1, 1]
    ramp = (u - enter) / max(1, mid)                                    # 0 -> 1
    centre = 0.5 + 0.5 * wob * np.sin(np.pi * ramp)                     # fades to ends
    pos = (1 - ramp) * 0 + ramp * 1                                     # baseline a -> b
    w = np.sin(np.pi * ramp)
    return a + span * float(np.clip((1 - w) * pos + w * centre, 0, 1))


def psola(src, marks, periods, voiced, segments, out, wsum, f0_at, sr,
          jitter=0.0015, shimmer=0.015, rng=None):
    """One continuous TD-PSOLA pass over a sentence.

    segments: sorted [(o0, o1, s0, s1, force_period)] mapping output sample
    ranges to source ranges. Running it as one pass (instead of piece by
    piece) keeps the grain sequence continuous across syllable parts, and
    wsum lets us normalise the overlap-add so raised notes don't get louder
    and rougher.

    Anti-metal: when a short vowel is stretched over a long note, plain PSOLA
    repeats the same few grains -> a perfectly periodic, metallic buzz. So a
    stretched vowel is read slowly back and forth through its steady middle
    (neighbouring grains still follow each other, so no roughness), and a
    little period jitter and amplitude shimmer is added like a real voice.
    """
    rng = rng or np.random.default_rng(7)
    if not segments:
        return
    win_cache = {}
    # vowels: trim to their voiced part (Piper often puts the pause before
    # punctuation inside the last vowel) and find the steady middle
    segs, regions, levels = [], [], []
    vr = [np.sqrt(np.mean(src[sg[2]:sg[3]] ** 2)) for sg in segments if sg[4] and sg[3] > sg[2]]
    vowel_rms = float(np.median(vr)) if vr else 0.0
    for o0, o1, s0, s1, force in segments:
        reg = lvl = None
        if force:
            vm = marks[(marks >= s0) & (marks <= s1) & voiced]
            weak = len(vm) < 4 or (s1 > s0 and np.sqrt(np.mean(src[s0:s1] ** 2)) < 0.3 * vowel_rms)
            if weak:
                # reduced vowels (ə in "upon") are often near-silent in TTS:
                # borrow the nearest steady voiced sound within 50 ms
                pad = int(0.05 * sr)
                reg = _steady_region(src, marks, periods, voiced, s0 - pad, s1 + pad)
                if reg is not None:
                    s0, s1 = int(reg[0]), int(reg[1])
            elif len(vm) >= 4:
                s0, s1 = int(vm[0]), int(vm[-1])
            reg = _steady_region(src, marks, periods, voiced, s0, s1)
            if reg is not None:
                r = src[int(reg[0]):int(reg[1]) + 1]
                lvl = float(np.sqrt(np.mean(r ** 2))) if len(r) else None
        segs.append((o0, o1, s0, s1, force)); regions.append(reg); levels.append(lvl)
    segments = segs
    known = [l for l in levels if l]
    ref = float(np.median(known)) if known else None   # sentence loudness target
    j = 0
    t = segments[0][0]
    while j < len(segments):
        o0, o1, s0, s1, force = segments[j]
        if t >= o1:
            j += 1
            continue
        if t < o0:
            t = o0
        s = _src_pos(t, o0, o1, s0, s1, force, regions[j], sr_guess=sr)
        k = int(np.clip(np.searchsorted(marks, s), 0, len(marks) - 1))
        if k > 0 and abs(marks[k - 1] - s) < abs(marks[k] - s):
            k -= 1
        m, p, v = marks[k], int(periods[k]), voiced[k]
        if not v and force:                 # vowel the pitch tracker missed
            p, v = force, True
        if v:
            f = f0_at(t / sr)
            tp = int(round(sr / f)) if f > 0 else p
            hw = min(p, tp)                  # window no wider than 2 target periods
        else:
            tp, hw = p, p
        a, b = max(0, m - hw), min(len(src), m + hw)
        L = b - a
        if L > 4:
            w = win_cache.get((L, m - a, hw))
            if w is None:                   # window centred on the mark
                full = np.hanning(2 * hw + 1)
                w = win_cache[(L, m - a, hw)] = full[hw - (m - a): hw - (m - a) + L]
            st = t - (m - a)
            lo, hi = max(0, st), min(len(out), st + L)
            if hi > lo:
                g = 1.0 + shimmer * rng.standard_normal() if v else 1.0
                if levels[j] and v:          # even out loudness in held vowels
                    grms = float(np.sqrt(np.mean(src[a:b] ** 2))) + 1e-6
                    g *= float(np.clip(levels[j] / grms, 0.6, 1.6)) ** 0.7
                    g *= float(np.clip(ref / levels[j], 0.5, 3.0)) ** 0.5  # weak syllables up
                out[lo:hi] += g * (src[a:b] * w)[lo - st: hi - st]
                wsum[lo:hi] += w[lo - st: hi - st]
        step = int(round(tp * (1 + jitter * rng.standard_normal()))) if v else p
        t += max(step, 8)


def midi_hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)
