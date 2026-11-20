"""Where songs come from - three places, the first match wins:

  1. your own: .kar/.mid files in the skill's `songs` folder on the device
     (and any folders in the "song_folders" setting);
  2. shipped with the skill (songs/ in the package) - always there, offline;
  3. the online songbook: songbook/index.json in this repository, fetched
     when a song isn't found locally, and each song downloaded the first
     time it is sung (then kept).

Songs are found by title, forgivingly ("daisy" finds "Daisy Bell").

A song can come in several languages: name.kar and name.<lang>.kar (e.g.
hey_emkay.da.kar). They count as one song; the version in the device's
language is sung, else the English one, else any.
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
    language: str = ""         # "en", "da", ... ("" = not said)
    tags: tuple = ()           # "christmas", "disco", ...
    words: frozenset = frozenset()   # the words of the lyrics, for searching
    key: str = ""              # the song, whatever the language: file name without .lang

    def __post_init__(self):
        if not self.key:
            name = self.path.name if self.path else self.url.rsplit("/", 1)[-1] or self.title
            self.key = name.split(".")[0].lower()


FILLER = {"the", "a", "an", "song", "songs", "tune", "about", "with", "some", "sang", "sangen",
          "sange", "en", "et", "den", "det", "om", "med", "nogle", "something", "for", "to", "me",
          "noget", "til", "mig"}


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(w for w in re.findall(r"[a-z0-9æøå]+", text) if w not in FILLER)


def short_title(title: str) -> str:
    """"Daisy Bell (Bicycle Built for Two)" -> "Daisy Bell", for speaking."""
    return re.sub(r"\s*\(.*?\)", "", title).strip() or title


def title_from_file(path: Path) -> str:
    return re.sub(r"[_\-]+", " ", path.stem).strip().title()


def kar_info(path: Path) -> dict:
    """Title, language, tags and lyric words of a .kar file: @T, @L and
    "@Itags:" lines, else the first track's name and the file name's
    .<lang> part."""
    info = {"title": "", "language": "", "tags": (), "words": frozenset()}
    parts = path.name.split(".")
    if len(parts) > 2 and len(parts[-2]) == 2:
        info["language"] = parts[-2].lower()
    try:
        import mido
        from .song import _lyric_events, _parse_syllables
        mid = mido.MidiFile(str(path))
    except Exception:
        info["title"] = title_from_file(path)
        return info
    for tr in mid.tracks:
        for m in tr:
            if m.type != "text":
                continue
            if m.text.startswith("@T") and not info["title"]:
                info["title"] = m.text[2:].strip()
            elif m.text.startswith("@L") and not info["language"]:
                lang = m.text[2:].strip().lower()
                info["language"] = {"engl": "en", "eng": "en"}.get(lang, lang[:2])
            elif m.text.lower().startswith("@itags:"):
                info["tags"] = tuple(t.strip().lower() for t in m.text[7:].split(",") if t.strip())
    if not info["title"]:
        for m in mid.tracks[0] if mid.tracks else []:
            if m.type == "track_name" and m.name.strip():
                info["title"] = m.name.strip()
                break
    words, cur = set(), ""
    for syl in _parse_syllables(_lyric_events(mid, lambda tick: tick)):
        if syl.word_start and cur:
            words.add(cur)
            cur = ""
        cur += syl.text
    words.add(cur)
    info["words"] = frozenset(normalise(w) for w in words if normalise(w))
    info["title"] = info["title"] or title_from_file(path)
    return info


def local_songs(folders: list[Path]) -> list[Entry]:
    out = []
    for where, folder in [("own", f) for f in folders] + [("shipped", PACKAGE_SONGS)]:
        if folder and folder.is_dir():
            for p in sorted(folder.iterdir()):
                if p.suffix.lower() in SUFFIXES:
                    i = kar_info(p)
                    out.append(Entry(i["title"], where, path=p, language=i["language"],
                                     tags=i["tags"], words=i["words"]))
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
    return [Entry(s["title"], "online", url=base + s["file"], language=s.get("language", ""),
                  tags=tuple(s.get("tags", ())), words=frozenset(s.get("words", ())))
            for s in index.get("songs", [])]


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


def version(entries: list[Entry], key: str, lang: str) -> Entry | None:
    """The version of song `key` to sing for a device speaking `lang`: own
    files first, then that language, then English, then any."""
    lang = (lang or "")[:2].lower()
    same = [e for e in entries if e.key == key]
    if not same:
        return None
    rank = {"own": 0, "shipped": 1, "online": 2}

    def pref(e):
        return (0 if e.language == lang else 1 if e.language in ("en", "") else 2, rank.get(e.where, 3))
    return min(same, key=pref)


def songs(entries: list[Entry], lang: str) -> list[Entry]:
    """One entry per song, in the version for `lang`."""
    keys = list(dict.fromkeys(e.key for e in entries))
    return [version(entries, k, lang) for k in keys]


def find(query: str, entries: list[Entry], lang: str = "", threshold: float = 0.5) -> Entry | None:
    """The song whose title (in any language) matches best, in the version for `lang`."""
    best = max(entries, key=lambda e: score(query, e.title), default=None)
    if best is None or score(query, best.title) < threshold:
        return None
    return version(entries, best.key, lang)


def search(query: str, entries: list[Entry], lang: str = "", threshold: float = 0.5) -> list[Entry]:
    """Songs that match a search ("christmas", "a song about the moon",
    "reggae"): by title, tag or words of the lyrics; best first, one version
    per song, in the version for `lang`."""
    q = set(normalise(query).split())
    if not q:
        return []
    by_key = {}
    for e in entries:
        tag_words = {w for t in e.tags for w in normalise(t).split()}
        sc = max(score(query, e.title),
                 len(q & tag_words) / len(q),
                 0.8 * len(q & e.words) / len(q))
        if sc >= threshold:
            by_key[e.key] = max(by_key.get(e.key, 0.0), sc)
    ranked = sorted(by_key, key=lambda k: -by_key[k])
    return [version(entries, k, lang) for k in ranked]
