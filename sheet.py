"""The karaoke screen: the line being sung, drawn as an SVG picture.

  chords      above, where they change
  melody      a small piano roll - one bar per note, higher notes higher
  lyrics      the syllables under their notes; what has been sung is lit
  next line   underneath, dimmed

Sent as a data: URI to the stock SYSTEM_ImageFrame page, so every OVOS GUI
can show it - nothing to install, nothing fetched.
"""
from __future__ import annotations

import base64
from html import escape

from .song import Chord, Syllable

W, H = 1280, 720
BG, FG, LIT, DIM, BAR, BAR_LIT, CHORD = ("#101418", "#e8e8e8", "#ffcc33", "#7a8088",
                                         "#3b4a5a", "#ffcc33", "#66d9ef")


def _text_w(s: str, size: float) -> float:
    return len(s) * size * 0.66


def _layout(line: list[Syllable], size: float, x0: float, x1: float):
    """x of each syllable: by time, but never overlapping the previous one."""
    if not line:
        return [], size
    t0, t1 = line[0].time, max(s.end for s in line)
    span = max(0.5, t1 - t0)
    for _ in range(6):
        xs, x = [], x0
        for i, s in enumerate(line):
            want = x0 + (s.time - t0) / span * (x1 - x0) * 0.85
            if not i:
                x = x0
            elif s.word_start:
                x = max(want, x + size * 0.45)
            else:   # same word: joined, or (a long note) pulled apart with a dash
                x = want if want - x > size * 0.6 else x
            xs.append(x)
            x += _text_w(s.text, size)
        if x <= x1:
            return xs, size
        size *= 0.88
    return xs, size


def render(title: str, line: list[Syllable], next_line: list[Syllable], chords: list[Chord],
           now: float, show_music: bool = True) -> str:
    size = 64.0
    xs, size = _layout(line, size, 70, W - 70)
    parts = [f'<rect width="{W}" height="{H}" fill="{BG}"/>',
             f'<text x="{W/2}" y="56" font-size="30" fill="{DIM}" text-anchor="middle" '
             f'font-family="sans-serif">{escape(title)}</text>']
    y_text = 470 if show_music else 380
    if show_music and line:
        pitches = [n.pitch for s in line for n in s.notes]
        if pitches:
            lo, hi = min(pitches), max(max(pitches), min(pitches) + 7)
            top, bottom = 170, 360
            for s, x in zip(line, xs):
                w_syl = max(_text_w(s.text, size), size * 0.6)
                k = len(s.notes)
                for j, n in enumerate(s.notes):
                    y = bottom - (n.pitch - lo) / (hi - lo) * (bottom - top)
                    lit = n.start <= now
                    parts.append(f'<rect x="{x + j * w_syl / k:.1f}" y="{y - 9:.1f}" '
                                 f'width="{max(8, w_syl / k - 6):.1f}" height="18" rx="9" '
                                 f'fill="{BAR_LIT if lit else BAR}"/>')
        # chords: at the syllable sounding when the chord starts
        if line:
            t_end = max(s.end for s in line)
            shown = [c for c in chords if line[0].time - 0.01 <= c.time < t_end]
            before = [c for c in chords if c.time < line[0].time - 0.01]
            if before and (not shown or shown[0].time > line[0].time + 0.05):
                shown.insert(0, Chord(line[0].time, before[-1].name))
            last_x = -1e9
            for c in shown:
                idx = max([i for i, s in enumerate(line) if s.time <= c.time + 0.05] or [0])
                x = max(xs[idx], last_x + 90)
                last_x = x
                parts.append(f'<text x="{x:.1f}" y="140" font-size="40" font-weight="bold" '
                             f'fill="{CHORD}" font-family="sans-serif">{escape(c.name)}</text>')
    # runs of syllables written together (one word, or the joined part of
    # one): one <text> each, a <tspan> per syllable, so they join exactly
    runs = []
    for i, (syl, x) in enumerate(zip(line, xs)):
        joined = i and not syl.word_start and xs[i] == runs[-1][-1][2]
        if joined:
            runs[-1].append((syl, x, x + _text_w(syl.text, size)))
        else:
            runs.append([(syl, x, x + _text_w(syl.text, size))])
    for r, run in enumerate(runs):
        spans = "".join(
            f'<tspan font-weight="{"bold" if syl.time <= now else "normal"}" '
            f'fill="{LIT if syl.time <= now else FG}">{escape(syl.text)}</tspan>'
            for syl, _, _ in run)
        parts.append(f'<text x="{run[0][1]:.1f}" y="{y_text}" font-size="{size:.0f}" '
                     f'font-family="sans-serif">{spans}</text>')
        nxt = runs[r + 1][0] if r + 1 < len(runs) else None
        if nxt and not nxt[0].word_start:          # a word pulled apart by a long note
            end = run[-1][2]
            lit = nxt[0].time <= now
            parts.append(f'<text x="{(end + nxt[1]) / 2:.1f}" y="{y_text}" '
                         f'font-size="{size:.0f}" font-family="sans-serif" text-anchor="middle" '
                         f'fill="{LIT if lit else FG}">-</text>')
    if next_line:
        text = line_text(next_line)
        nsize = min(44.0, (W - 140) / max(1, len(text) * 0.6))
        parts.append(f'<text x="{W/2}" y="{y_text + 130}" font-size="{nsize:.0f}" text-anchor="middle" '
                     f'font-family="sans-serif" fill="{DIM}">{escape(text)}</text>')
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">'
           + "".join(parts) + "</svg>")
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


def line_text(line: list[Syllable]) -> str:
    out = ""
    for s in line:
        out += (" " if s.word_start and out else "") + s.text
    return out


STYLE_COLOURS = {"disco": "#ff66cc", "rock": "#ff5544", "reggae": "#44cc66", "lullaby": "#8899ff",
                 "christmas": "#ee3344", "waltz": "#ffcc33"}


def card(title: str, tags=()) -> str:
    """A square tile for a song in a list: its title on a colour of its kind."""
    colour = next((c for t, c in STYLE_COLOURS.items() if any(t in x for x in tags)), CHORD)
    words, lines = title.split(), [""]
    for w in words:                                  # wrap at ~12 characters
        if lines[-1] and len(lines[-1]) + len(w) > 12:
            lines.append("")
        lines[-1] = (lines[-1] + " " + w).strip()
    y0 = 200 - (len(lines) - 1) * 26
    text = "".join(f'<text x="200" y="{y0 + i * 52}" font-size="44" font-weight="bold" '
                   f'text-anchor="middle" font-family="sans-serif" fill="{BG}">{escape(ln)}</text>'
                   for i, ln in enumerate(lines))
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 400">'
           f'<rect width="400" height="400" rx="60" fill="{colour}"/>{text}</svg>')
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")
