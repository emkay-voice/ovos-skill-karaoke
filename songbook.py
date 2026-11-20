"""Where songs come from - three places, the first match wins:

  1. your own: .kar/.mid files in the skill's `songs` folder on the device
     (and any folders in the "song_folders" setting);
  2. shipped with the skill (songs/ in the package) - always there, offline;
  3. the online songbook: songbook/index.json in this repository, fetched
     when a song isn't found locally, and each song downloaded the first
     time it is sung (then kept).

Songs are found by title, forgivingly ("daisy" finds "Daisy Bell").
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

PACKAGE_SONGS = Path(__file__).resolve().parent / "songs"
SUFFIXES = {".kar", ".mid", ".midi"}


@dataclass
class Entry:
    title: str
    where: str                 # "own", "shipped", "online"
    path: Path | None = None   # local file
    url: str = ""              # online


FILLER = {"the", "a", "song", "tune", "sangen", "en", "et", "den", "det", "om"}


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(w for w in re.findall(r"[a-z0-9æøå]+", text) if w not in FILLER)


def short_title(title: str) -> str:
    """"Daisy Bell (Bicycle Built for Two)" -> "Daisy Bell", for speaking."""
    return re.sub(r"\s*\(.*?\)", "", title).strip() or title


def title_from_file(path: Path) -> str:
    return re.sub(r"[_\-]+", " ", path.stem).strip().title()


def kar_title(path: Path) -> str:
    """The @T title of a .kar file, else the first track's name, else the
    file name."""
    try:
        import mido
        mid = mido.MidiFile(str(path))
    except Exception:
        return title_from_file(path)
    for tr in mid.tracks:
        for m in tr:
            if m.type == "text" and m.text.startswith("@T"):
                return m.text[2:].strip()
    for m in mid.tracks[0] if mid.tracks else []:
        if m.type == "track_name" and m.name.strip():
            return m.name.strip()
    return title_from_file(path)


def local_songs(folders: list[Path]) -> list[Entry]:
    out = []
    for where, folder in [("own", f) for f in folders] + [("shipped", PACKAGE_SONGS)]:
        if folder and folder.is_dir():
            for p in sorted(folder.iterdir()):
                if p.suffix.lower() in SUFFIXES:
                    out.append(Entry(kar_title(p), where, path=p))
    return out


def online_songs(base_url: str, fetch) -> list[Entry]:
    """The online songbook's index; `fetch(url) -> bytes`."""
    if not base_url:
        return []
    base = base_url.rstrip("/") + "/"
    try:
        index = json.loads(fetch(base + "index.json"))
    except Exception:
        return []
    return [Entry(s["title"], "online", url=base + s["file"]) for s in index.get("songs", [])]


def score(query: str, title: str) -> float:
    """How well a spoken name matches a title (0..1): the whole title, the
    title without its "(...)" part, or that part alone."""
    variants = [title, re.sub(r"\(.*?\)", " ", title)] + re.findall(r"\((.*?)\)", title)
    return max(_score(query, v) for v in variants)


def _score(query: str, title: str) -> float:
    q, t = normalise(query), normalise(title)
    if not q or not t:
        return 0.0
    if q == t:
        return 1.0
    if q in t or t in q:
        return 0.9 * min(len(q), len(t)) / max(len(q), len(t)) + 0.1
    qw, tw = set(q.split()), set(t.split())
    common = len(qw & tw)
    if not common:
        from difflib import SequenceMatcher
        return 0.6 * SequenceMatcher(None, q, t).ratio()
    return common / max(len(qw), len(tw))


def find(query: str, entries: list[Entry], threshold: float = 0.5) -> Entry | None:
    best = max(entries, key=lambda e: score(query, e.title), default=None)
    return best if best is not None and score(query, best.title) >= threshold else None
