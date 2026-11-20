"""The band: plays any General MIDI file to audio.

Two ways, so it works on every OVOS:

  * FluidSynth, when the device has the `fluidsynth` program and a SoundFont
    (real instrument samples - the best sound);
  * otherwise a small General MIDI synthesizer in numpy: an instrument for
    each GM family (piano, electric piano, clavinet, bells, organ, accordion,
    guitar, bass, strings, brass, reeds, flutes, synth leads and pads) and a
    drum kit, plus a little room reverb. It needs nothing installed, so it
    also runs in a browser (Emkay).

A rendered backing is cached by the caller; rendering is the slow part.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import mido
import numpy as np

from .song import _seconds, _tempo_map

SR = 24000
SOUNDFONTS = [  # where distributions put a General MIDI SoundFont
    "/usr/share/sounds/sf2/FluidR3_GM.sf2", "/usr/share/soundfonts/FluidR3_GM.sf2",
    "/usr/share/sounds/sf2/default-GM.sf2", "/usr/share/soundfonts/default.sf2",
    "/usr/share/sounds/sf2/TimGM6mb.sf2", "/usr/share/soundfonts/GeneralUser-GS.sf2",
]


def hz(p):
    return 440.0 * 2 ** ((p - 69) / 12)


# ------------------------------------------------------------- instruments
def _env(n, attack, release, sustain_n):
    """Attack ramp, hold, release ramp after sustain_n samples."""
    t = np.arange(n)
    a = max(1, int(attack * SR))
    r = max(1, int(release * SR))
    e = np.minimum(1.0, t / a)
    rel = np.clip(1.0 - (t - sustain_n) / r, 0, 1)
    return e * np.where(t < sustain_n, 1.0, rel)


def _additive(f, n, amps, decays=None, detune=0.0, vib=0.0, inharm=0.0, phase_rand=True,
              rng=np.random.default_rng(1)):
    """Sum of harmonics k*f with amplitude amps[k-1] and exp decay rates."""
    t = np.arange(n) / SR
    out = np.zeros(n)
    fm = 1.0 + (vib * np.sin(2 * np.pi * 5.2 * t) if vib else 0.0)
    for k, a in enumerate(amps, start=1):
        fk = f * k * (1 + inharm * k * k)
        if fk >= SR / 2 or a == 0:
            continue
        ph = 2 * np.pi * np.cumsum(np.full(n, fk) * fm) / SR if vib else 2 * np.pi * fk * t
        p0 = rng.uniform(0, 2 * np.pi) if phase_rand else 0.0
        w = a * np.sin(ph + p0)
        if decays is not None:
            w *= np.exp(-decays[k - 1] * t)
        out += w
        if detune:
            out += a * np.sin(2 * np.pi * fk * (1 + detune) * t + p0 * 1.3) * (
                np.exp(-decays[k - 1] * t) if decays is not None else 1.0)
    return out


def _lowpass(x, k):
    if k <= 1:
        return x
    c = np.cumsum(np.concatenate([np.zeros(k), x]))
    return (c[k:] - c[:-k]) / k


def _pluck(f, n, damp=0.996, bright=0.5, rng=np.random.default_rng(2)):
    """Karplus-Strong string, a period block at a time (vectorised)."""
    p = max(2, int(round(SR / f)))
    buf = rng.uniform(-1, 1, p)
    buf = _lowpass(buf, max(1, int(3 * (1 - bright)) + 1))
    blocks, have = [], 0
    while have < n:
        blocks.append(buf)
        have += p
        buf = damp * 0.5 * (buf + np.roll(buf, 1))
    return np.concatenate(blocks)[:n]


def instrument(prog, pitch, dur, vel):
    """One note of GM program `prog` (0-127)."""
    f = hz(pitch)
    fam = prog // 8
    rel = 0.12
    hold = int(dur * SR)
    if fam in (0,):                              # piano
        rel = 0.25
        n = hold + int(rel * SR)
        k = np.arange(1, 13)
        dec = 0.8 + 0.25 * k + f / 600
        y = _additive(f, n, 1 / k ** 1.4, dec, inharm=0.0003)
        y *= _env(n, 0.003, rel, hold)
    elif prog in (4, 5):                         # electric piano
        rel = 0.3
        n = hold + int(rel * SR)
        y = _additive(f, n, [1, 0.25, 0.08, 0, 0, 0, 0.15], [0.9, 2, 4, 1, 1, 1, 9])
        y *= _env(n, 0.004, rel, hold)
    elif prog in (6, 7):                         # harpsichord / clavinet: bright, short
        n = hold + int(0.06 * SR)
        k = np.arange(1, 21)
        amps = np.abs(np.sin(np.pi * k * 0.22)) / k ** 0.8
        y = _additive(f, n, amps, 6 + 0.6 * k)
        y *= _env(n, 0.002, 0.06, hold)
    elif fam == 1:                               # bells: glockenspiel, music box, vibes
        rel = 0.8
        n = hold + int(rel * SR)
        t = np.arange(n) / SR
        partials = [(1, 1.0, 2.2), (2.76, 0.45, 5), (5.4, 0.25, 9), (8.93, 0.12, 14)]
        y = sum(a * np.sin(2 * np.pi * f * r * t) * np.exp(-d * t) for r, a, d in partials
                if f * r < SR / 2)
        if prog == 11:                           # vibraphone tremolo
            y *= 1 - 0.3 * (1 + np.sin(2 * np.pi * 6 * t)) / 2
        y = y * _env(n, 0.001, rel, n)
    elif fam == 2:                               # organs and accordion
        rel = 0.08
        n = hold + int(rel * SR)
        if prog in (21, 22, 23):                 # accordion / harmonica: two reeds
            k = np.arange(1, 13)
            y = _additive(f, n, 1 / k, detune=0.004, vib=0.002)
        else:
            y = _additive(f, n, [1, 0.8, 0.5, 0.4, 0, 0.3, 0, 0.2])
        y *= _env(n, 0.01, rel, hold)
    elif fam == 3:                               # guitars
        rel = 0.1
        n = hold + int(rel * SR)
        y = _pluck(f, n, damp=0.997 if prog < 29 else 0.999, bright=0.6 if prog < 29 else 0.9)
        if prog >= 29:                           # overdrive
            y = np.tanh(3 * y)
        y *= _env(n, 0.001, rel, hold)
    elif fam == 4:                               # basses
        rel = 0.06
        n = hold + int(rel * SR)
        if prog in (38, 39):                     # synth bass: saw with a closing filter
            k = np.arange(1, 16)
            y = _additive(f, n, 1 / k, 3 + 2.2 * k, phase_rand=False)
        else:
            k = np.arange(1, 9)
            y = _additive(f, n, 1 / k ** 1.6, 1.5 + 1.2 * k) + 0.4 * _pluck(f, n, 0.995, 0.3)
        y *= _env(n, 0.004, rel, hold)
    elif fam in (5, 6, 11):                      # strings, ensembles/choir, pads
        rel = 0.35
        n = hold + int(rel * SR)
        k = np.arange(1, 17)
        amps = 1 / k * np.exp(-k / 7)
        if prog in (45,):                        # pizzicato
            n = hold + int(0.1 * SR)
            y = _pluck(f, n, 0.993, 0.4) * _env(n, 0.002, 0.1, hold)
        else:
            y = _additive(f, n, amps, detune=0.006, vib=0.003)
            y += _additive(f, n, amps * 0.7, detune=-0.004)
            y *= _env(n, 0.12 if prog not in (49, 55) else 0.03, rel, hold)
    elif fam == 7:                               # brass: bright attack
        rel = 0.1
        n = hold + int(rel * SR)
        k = np.arange(1, 15)
        t = np.arange(n) / SR
        y = sum((1 / kk) * np.sin(2 * np.pi * f * kk * t) * (0.4 + 0.6 * np.exp(-kk * 4 * t))
                for kk in k if f * kk < SR / 2)
        y = y * _env(n, 0.025, rel, hold)
    elif fam == 8:                               # reeds (sax, clarinet, oboe)
        rel = 0.08
        n = hold + int(rel * SR)
        k = np.arange(1, 13)
        amps = np.where(k % 2 == 1, 1 / k, 0.15 / k) if prog == 71 else 1 / k ** 1.2
        y = _additive(f, n, amps, vib=0.004) * _env(n, 0.03, rel, hold)
    elif fam == 9:                               # flutes, recorder, whistle
        rel = 0.1
        n = hold + int(rel * SR)
        y = _additive(f, n, [1, 0.12, 0.05], vib=0.005)
        y += 0.03 * _lowpass(np.random.default_rng(3).standard_normal(n), 4)
        y *= _env(n, 0.04, rel, hold)
    elif fam == 10:                              # synth leads
        rel = 0.1
        n = hold + int(rel * SR)
        k = np.arange(1, 20)
        amps = np.where(k % 2 == 1, 1 / k, 0) if prog == 80 else 1 / k
        y = _additive(f, n, amps * np.exp(-k / 12), detune=0.003, vib=0.004)
        y *= _env(n, 0.01, rel, hold)
    else:                                        # everything else: a soft pad
        rel = 0.2
        n = hold + int(rel * SR)
        y = _additive(f, n, [1, 0.4, 0.2, 0.1], vib=0.003) * _env(n, 0.05, rel, hold)
    peak = np.abs(y).max() or 1.0
    return y / peak * (vel / 127) ** 1.6


def drum(key, vel, rng=np.random.default_rng(4)):
    """One hit of the GM drum kit (channel 10)."""
    def noise(sec):
        return rng.uniform(-1, 1, int(sec * SR))

    def hp(x):
        return np.diff(x, prepend=0.0)

    def t_of(x):
        return np.arange(len(x)) / SR

    if key in (35, 36):                                   # kick
        n = int(0.35 * SR)
        t = np.arange(n) / SR
        f = 45 + 95 * np.exp(-t * 28)
        y = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 9)
        y[: int(0.004 * SR)] += 0.4 * noise(0.004)
    elif key in (38, 40):                                 # snare
        x = noise(0.22)
        t = t_of(x)
        y = 0.7 * _lowpass(hp(x), 2) * np.exp(-t * 18) + 0.5 * np.sin(2 * np.pi * 185 * t) * np.exp(-t * 30)
    elif key == 39:                                       # hand clap: a few bursts
        x = hp(noise(0.25))
        t = t_of(x)
        env = sum(np.exp(-np.clip(t - d, 0, None) * 120) * (t >= d) for d in (0, 0.011, 0.022))
        y = x * (0.6 * env + 0.4 * np.exp(-np.clip(t - 0.03, 0, None) * 14) * (t >= 0.03))
    elif key in (42, 44):                                 # closed / pedal hi-hat
        x = hp(hp(noise(0.06)))
        y = x * np.exp(-t_of(x) * 70)
    elif key == 46:                                       # open hi-hat
        x = hp(hp(noise(0.35)))
        y = x * np.exp(-t_of(x) * 9)
    elif key in (49, 52, 55, 57):                         # crash cymbals
        x = hp(hp(noise(1.6)))
        y = x * np.exp(-t_of(x) * 2.5)
    elif key in (51, 53, 59):                             # ride
        x = hp(hp(noise(0.8)))
        t = t_of(x)
        y = x * np.exp(-t * 5) * 0.6 + 0.2 * np.sin(2 * np.pi * 5200 * t) * np.exp(-t * 6)
    elif key == 54:                                       # tambourine
        x = hp(hp(noise(0.18)))
        y = x * np.exp(-t_of(x) * 22)
    elif key in (83, 81, 80):                             # jingle bell (GS) / triangles
        n = int(0.25 * SR)
        t = np.arange(n) / SR
        y = np.zeros(n)
        for _ in range(6):                                # a few little bells, a hair apart
            d = rng.uniform(0, 0.012)
            fr = rng.uniform(3800, 6800)
            on = t >= d
            y += on * np.sin(2 * np.pi * fr * (t - d)) * np.exp(-np.clip(t - d, 0, None) * 30)
        y += 0.3 * hp(hp(rng.uniform(-1, 1, n))) * np.exp(-t * 40)
    elif key == 37:                                       # side stick
        x = noise(0.04)
        y = hp(x) * np.exp(-t_of(x) * 90)
    elif 41 <= key <= 50:                                 # toms
        n = int(0.4 * SR)
        t = np.arange(n) / SR
        base = 70 + (key - 41) * 18
        y = np.sin(2 * np.pi * np.cumsum(base * (1 + 0.6 * np.exp(-t * 20))) / SR) * np.exp(-t * 8)
    else:                                                 # other percussion: a click
        x = noise(0.05)
        y = hp(x) * np.exp(-t_of(x) * 60)
    peak = np.abs(y).max() or 1.0
    return y / peak * (vel / 127) ** 1.5


DRUM_GAIN = {36: 1.0, 35: 1.0, 38: 0.55, 40: 0.55, 39: 0.5, 42: 0.22, 44: 0.2, 46: 0.25,
             49: 0.35, 57: 0.35, 51: 0.25, 54: 0.25, 83: 0.3}
FAMILY_GAIN = {0: 0.5, 1: 0.35, 2: 0.32, 3: 0.4, 4: 0.75, 5: 0.22, 6: 0.25, 7: 0.3,
               8: 0.35, 9: 0.4, 10: 0.3, 11: 0.22}


# ------------------------------------------------------------- the score
def notes(mid: mido.MidiFile, skip_tracks=()):
    """[(start_s, end_s, channel, program, pitch, velocity, volume)] with the
    sustain pedal applied."""
    sec = _seconds(_tempo_map(mid), mid.ticks_per_beat)
    out = []
    for ti, tr in enumerate(mid.tracks):
        if ti in skip_tracks:
            continue
        prog, vol, expr = [0] * 16, [100] * 16, [127] * 16
        pedal, held, on = [False] * 16, {}, {}
        t = 0
        for m in tr:
            t += m.time
            if m.type == "program_change":
                prog[m.channel] = m.program
            elif m.type == "control_change":
                if m.control == 7:
                    vol[m.channel] = m.value
                elif m.control == 11:
                    expr[m.channel] = m.value
                elif m.control == 64:
                    pedal[m.channel] = m.value >= 64
                    if not pedal[m.channel]:
                        for k, (st, p, v, g) in list(held.items()):
                            if k[0] == m.channel:
                                out.append((sec(st), sec(t), k[0], p, k[1], v, g))
                                del held[k]
            elif m.type == "note_on" and m.velocity > 0:
                on[(m.channel, m.note)] = (t, prog[m.channel], m.velocity,
                                           vol[m.channel] * expr[m.channel] / 127 / 127)
            elif m.type in ("note_off", "note_on"):
                k = (m.channel, m.note)
                if k in on:
                    st, p, v, g = on.pop(k)
                    if pedal[m.channel]:
                        held[k] = (st, p, v, g)
                    else:
                        out.append((sec(st), sec(t), k[0], p, k[1], v, g))
        for k, (st, p, v, g) in list(on.items()) + list(held.items()):
            out.append((sec(st), sec(st) + 0.5, k[0], p, k[1], v, g))
    return sorted(out)


def _reverb(x, wet=0.16, seconds=1.1):
    rng = np.random.default_rng(5)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    ir = rng.standard_normal(n) * np.exp(-t * 6.0 / seconds)
    ir = _lowpass(ir, 3)
    ir[: int(0.012 * SR)] = 0                     # a little pre-delay
    ir /= np.sqrt((ir ** 2).sum())
    size = 1 << int(np.ceil(np.log2(len(x) + n)))
    y = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)[: len(x)]
    return x + wet * y


def render_numpy(mid: mido.MidiFile, skip_tracks=(), length=None):
    ev = notes(mid, skip_tracks)
    length = length or (max((e[1] for e in ev), default=0) + 2.0)
    out = np.zeros(int(length * SR) + SR)
    cache = {}
    for st, en, ch, prog, pitch, vel, gain in ev:
        dur = max(0.03, en - st)
        if ch == 9:
            key = ("d", pitch, vel // 8)
            y = cache.get(key)
            if y is None:
                y = cache[key] = drum(pitch, vel) * DRUM_GAIN.get(pitch, 0.3)
        else:
            key = (prog, pitch, round(dur, 2), vel // 8)
            y = cache.get(key)
            if y is None:
                y = cache[key] = instrument(prog, pitch, dur, vel) * FAMILY_GAIN.get(prog // 8, 0.3)
        a = int(st * SR)
        b = min(len(out), a + len(y))
        if b > a:
            out[a:b] += y[: b - a] * gain
    out = _reverb(out)
    peak = np.abs(out).max() or 1.0
    return np.tanh(out / peak * 1.2) / np.tanh(1.2) * 0.9


def find_soundfont(configured: str = "") -> str | None:
    for p in [configured] + SOUNDFONTS:
        if p and os.path.isfile(os.path.expanduser(p)):
            return os.path.expanduser(p)
    return None


def render_fluidsynth(mid: mido.MidiFile, soundfont: str, skip_tracks=()):
    """FluidSynth, when the device has it: returns samples at SR."""
    import wave
    m2 = mido.MidiFile(type=mid.type, ticks_per_beat=mid.ticks_per_beat)
    for i, tr in enumerate(mid.tracks):
        if i in skip_tracks:   # keep its timing, drop its notes
            t2 = mido.MidiTrack()
            acc = 0
            for m in tr:
                acc += m.time
                if m.is_meta:
                    t2.append(m.copy(time=acc))
                    acc = 0
            m2.tracks.append(t2)
        else:
            m2.tracks.append(tr)
    with tempfile.TemporaryDirectory() as d:
        mp, wp = Path(d) / "song.mid", Path(d) / "song.wav"
        m2.save(str(mp))
        subprocess.run(["fluidsynth", "-ni", "-g", "0.6", "-r", str(SR), "-F", str(wp),
                        soundfont, str(mp)], check=True, capture_output=True, timeout=600)
        with wave.open(str(wp)) as w:
            ch = w.getnchannels()
            data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float64)
    if ch == 2:
        data = data.reshape(-1, 2).mean(axis=1)
    return data / 32768.0


def render(mid: mido.MidiFile, skip_tracks=(), soundfont: str = ""):
    """The backing as mono float samples at SR."""
    sf = find_soundfont(soundfont) if shutil.which("fluidsynth") else None
    if sf:
        try:
            return render_fluidsynth(mid, sf, skip_tracks)
        except Exception:
            pass
    return render_numpy(mid, skip_tracks)


def transposed(mid: mido.MidiFile, semitones: int) -> mido.MidiFile:
    """The same song in another key (drums stay)."""
    if not semitones:
        return mid
    out = mido.MidiFile(type=mid.type, ticks_per_beat=mid.ticks_per_beat)
    for tr in mid.tracks:
        out.tracks.append(mido.MidiTrack(
            m.copy(note=max(0, min(127, m.note + semitones)))
            if m.type in ("note_on", "note_off") and m.channel != 9 else m for m in tr))
    return out
