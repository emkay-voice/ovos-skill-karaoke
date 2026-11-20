"""ovos-skill-karaoke: OVOS sings - or you do.

  "sing Daisy Bell"       the device sings it with its own voice, autotuned
                          onto the melody, with a band behind it
  "karaoke Jingle Bells"  the band plays (with the melody as a guide) and the
                          screen shows the words, the chords and the melody
  "what songs can you sing"
  "find a song about christmas"
  "can you sing a song?"  it's a little shy at first - say "come on"

Songs are MIDI-karaoke (.kar) files: a few ship with the skill, more come
from the online songbook, and your own go in the skill's `songs` folder.
Everything runs on the device: the voice is the device's own TTS, sung in
Python (numpy); the band is ovos-phal-plugin-midi, the device's MIDI
instrument (its own synth, FluidSynth, or a real instrument), which plays
the song's file and the voice in time with it. So it works on any OVOS - a
Mark II, a Raspberry Pi, a PC, Docker, or Emkay in a browser.

The pieces: song.py (reading .kar), nuclei.py + singer.py + dsp.py (the
singing voice), stage.py (getting a song ready, the voice's cache),
sheet.py (the screen), songbook.py (finding songs), voice.py (the TTS).
"""
from __future__ import annotations

import base64
import random
import threading
import time
from pathlib import Path

from ovos_bus_client.message import Message
from ovos_workshop.decorators import intent_handler
from ovos_workshop.skills import OVOSSkill

from . import sheet, songbook, stage
from .singer import Singer
from .voice import device_voice, voice_id

SONGBOOK_URL = "https://raw.githubusercontent.com/emkay-voice/ovos-skill-karaoke/main/songbook/"
#: hosts may show the words, e.g. under the face while the device sings
LYRICS_MSG = "ovos.karaoke.lyrics"
ENDED_MSG = "ovos.karaoke.ended"
MIDI_WAIT = 3.0           # seconds to wait for the MIDI plugin to answer
START_AFTER = 1.0         # the song starts this long after it is sent (plus the plugin's latency)
SHY_AFTER = 3600          # shy again when it hasn't sung for an hour
#: a song's tag: it begins by giving in ("Okay... okay, I'll sing"), so it is
#: the one sung after "can you sing a song?" - "no" - "come on"
COAXED = "coaxed"


class KaraokeSkill(OVOSSkill):

    def __init__(self, *args, **kwargs):
        # before OVOSSkill's own setup: its intents are registered, and can
        # be asked, before initialize() has run (on a slow device for a while)
        self._singers = {}
        self._run = None          # threading.Event of the running performance
        self._last_sung = 0.0     # when it last sang (it's shy when it hasn't for a while)
        self._lock = threading.Lock()
        super().__init__(*args, **kwargs)

    def initialize(self):
        self.settings.setdefault("songbook_url", SONGBOOK_URL)
        self.settings.setdefault("song_folders", [])
        self.settings.setdefault("sing_screen", "face")    # or "lyrics"
        (self.own_folder).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ songs
    @property
    def own_folder(self) -> Path:
        """Where your own .kar files go (the skill's data folder, songs/)."""
        return Path(self.file_system.path) / "songs"

    def _fetch(self, url: str) -> bytes:
        import requests
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        return r.content

    def _entries(self, online: bool = True):
        folders = [self.own_folder] + [Path(p).expanduser() for p in self.settings.get("song_folders", [])]
        found = songbook.local_songs(folders)
        if online:
            known = {(e.key, e.language) for e in found}
            found += [e for e in songbook.online_songs(self.settings.get("songbook_url"), self._fetch)
                      if (e.key, e.language) not in known]
        return found

    def _find(self, name: str):
        local = self._entries(online=False)
        return songbook.find(name, local, self.lang) or songbook.find(name, self._entries(online=True), self.lang)

    def _song_bytes(self, entry) -> bytes:
        if entry.path:
            return entry.path.read_bytes()
        cached = Path(self.file_system.path) / "online" / entry.url.rsplit("/", 1)[-1]
        if not cached.exists():
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(self._fetch(entry.url))
        return cached.read_bytes()

    def _pick_any(self, tag: str = ""):
        entries = songbook.songs(self._entries(online=False) or self._entries(online=True), self.lang)
        if tag:      # in the user's own language only: its first words are spoken
            entries = [e for e in entries if tag in e.tags and e.language == self.lang[:2]]
        return random.choice(entries) if entries else None

    # ------------------------------------------------------------ intents
    @intent_handler("sing.intent")
    def handle_sing(self, message):
        if not songbook.normalise(message.data.get("song", "")):
            # "can you sing a song?" also matches "can you sing {song}"
            # (song: "a song"): that is the shy question, not a title
            return self.handle_sing_any(message)
        self._start(message.data.get("song", ""), "sing")

    @intent_handler("sing_any.intent")
    def handle_sing_any(self, message):
        """"Can you sing a song?" - a little shy, as Mycroft was ("I'm a bit
        nervous about my voice, but here goes nothing"): it says no, and
        sings when you encourage it."""
        if time.time() - self._last_sung > SHY_AFTER:
            answer = self.get_response("shy", num_retries=0)
            if not answer or not self.voc_match(answer, "encourage"):
                self.speak_dialog("maybe_later")
                return
            # a song made for this moment begins by giving in itself ("Okay...
            # okay, I'll sing. But first, drums!"); else, as Mycroft did
            coaxed = self._pick_any(tag=COAXED)
            if coaxed:
                self._start("", "sing", entry=coaxed, quiet=True)
                return
            self.speak_dialog("okay_then", wait=True)
        self._start("", "sing")

    @intent_handler("karaoke.intent")
    def handle_karaoke(self, message):
        self._start(message.data.get("song", ""), "karaoke")

    @intent_handler("karaoke_any.intent")
    def handle_karaoke_any(self, message):
        self._start("", "karaoke")

    @intent_handler("find_song.intent")
    def handle_find(self, message):
        query = message.data.get("query", "")
        found = songbook.search(query, self._entries(), self.lang)
        if not found:
            self.speak_dialog("no_match", {"query": query})
            return
        titles = [songbook.short_title(e.title) for e in found[:6]]
        self._show_list(found[:6])
        if len(found) == 1:
            if self.ask_yesno("found_one", {"song": titles[0]}) == "yes":
                self._start(found[0].title, "sing")
            return
        answer = self.get_response("found_many", {"songs": self._join(titles)}, num_retries=0)
        pick = songbook.find(answer or "", found, self.lang) if answer else None
        if pick:
            self._start(pick.title, "karaoke" if self.voc_match(answer, "karaoke") else "sing")
        else:
            self.gui.release()

    def _show_list(self, entries):
        """The songs found, as tiles on the screen (stock SYSTEM_grid)."""
        self.gui["title"] = ""
        self.gui["items"] = [{"image": sheet.card(songbook.short_title(e.title), e.tags),
                              "title": songbook.short_title(e.title)} for e in entries]
        self.gui.show_page("SYSTEM_grid", override_idle=60)

    @intent_handler("list_songs.intent")
    def handle_list(self, message):
        titles = sorted({songbook.short_title(e.title) for e in songbook.songs(self._entries(), self.lang)})
        if not titles:
            self.speak_dialog("no_songs")
            return
        self.speak_dialog("song_list", {"songs": self._join(titles)})

    def _join(self, items):
        items = list(items)
        if len(items) == 1:
            return items[0]
        return ", ".join(items[:-1]) + f" {self.dialog_renderer.render('and', {})} " + items[-1]

    # ------------------------------------------------------------ performing
    def _start(self, name: str, mode: str, entry=None, quiet=False):
        """`quiet`: no "warming up my voice for ..." when the voice is ready
        (the song starts by talking itself)."""
        if not songbook.normalise(name):          # "sing a song" read as song="a song"
            name = ""
        entry = entry or (self._find(name) if name else self._pick_any())
        if entry is None:
            local = sorted({songbook.short_title(e.title) for e in songbook.songs(self._entries(), self.lang)})
            self.speak_dialog("unknown_song", {"song": name, "songs": self._join(local[:5])})
            return
        self.stop()
        band = self._band()
        if not band or (mode == "sing" and not band.get("clips")):
            self.speak_dialog("no_midi")
            return
        title = songbook.short_title(entry.title)
        if not (quiet and stage.ready(self._song_bytes(entry), mode, self._cache, voice_id(self.lang))):
            self.speak_dialog("warming_up" if mode == "sing" else "setting_up", {"song": title}, wait=True)
        try:
            perf = self._prepare(entry, mode)
        except Exception as e:
            self.log.exception(f"could not prepare {entry.title}: {e}")
            self.speak_dialog("no_voice" if mode == "sing" else "no_band")
            return
        if mode == "sing":
            self._last_sung = time.time()
        self._perform(perf, title, band)

    def _band(self):
        """ovos-phal-plugin-midi's status, or None when it doesn't answer
        (not installed, or not started yet: OVOS' PHAL service starts it)."""
        r = self.bus.wait_for_response(Message("ovos.midi.status"), "ovos.midi.status.response",
                                       timeout=MIDI_WAIT)
        if r is None or r.data.get("engine") in (None, "none"):
            return None
        return r.data

    @property
    def _cache(self) -> Path:
        return Path(self.file_system.path) / "cache"

    def _singer(self) -> Singer:
        vid = voice_id(self.lang)
        if vid not in self._singers:      # keeps its spoken words between songs
            self._singers[vid] = Singer(device_voice(self.bus, self.lang), self.lang)
        return self._singers[vid]

    def _prepare(self, entry, mode):
        data = self._song_bytes(entry)
        # karaoke too: a song may have lines the device says ("first, drums!")
        return stage.prepare(data, mode, self._cache, singer=self._singer(),
                             voice_id=voice_id(self.lang), title=entry.title)

    def _perform(self, perf, title, band):
        """The band (the song's file) and, when Emkay sings, the voice (a
        clip): sent to the MIDI plugin to start at the same moment."""
        run = threading.Event()
        with self._lock:
            self._run = run
        at = time.time() + float(band.get("latency", 0.3)) + START_AFTER
        if perf.vocal and band.get("clips"):
            clip = {"source": self.skill_id, "at": at, "path": str(perf.vocal)}
            r = self.bus.wait_for_response(Message("ovos.midi.clip", clip), "ovos.midi.clip.response",
                                           timeout=START_AFTER / 2)
            if r is None or r.data.get("error"):
                # the plugin can't read our folder (e.g. another container): the voice itself
                clip.pop("path")
                clip["data"] = base64.b64encode(perf.vocal.read_bytes()).decode()
                self.bus.emit(Message("ovos.midi.clip", clip))
        self.bus.emit(Message("ovos.midi.play", {
            "source": self.skill_id, "at": at, "data": base64.b64encode(perf.data).decode(),
            "transpose": perf.transpose, "mute": perf.mute}))
        t0 = time.monotonic() + (at - time.time())       # the song's time 0, on this clock
        threading.Thread(target=self._follow, args=(perf, title, t0, run), daemon=True).start()

    def _follow(self, perf, title, t0, run):
        """Keep the screen (and the words on the bus) in time with the song."""
        song, lines = perf.song, perf.song.lines
        show_sheet = perf.mode == "karaoke" or self.settings.get("sing_screen") == "lyrics"
        # the first line at once (read along during the intro), each line
        # again just before its first word, the sheet at every syllable, and
        # the lines the device says ("first we need some drums!") at theirs
        events = [(0.0, "start", "")] + [(max(0.0, ln[0].time - 0.6), "line", "") for ln in lines] + \
                 [(s.time, "syllable", "") for s in song.syllables] + \
                 [(t, "say", text) for t, text in song.spoken]
        events.sort(key=lambda e: e[0])
        line_idx = -1
        shown = False
        for t, kind, text in events:
            delay = t - (time.monotonic() - t0)
            if run.wait(max(0.0, delay)):
                return
            now = time.monotonic() - t0
            if kind == "say":
                upcoming = next((ln for ln in lines if ln[0].time > now), [])
                self.bus.emit(Message(LYRICS_MSG, {"title": title, "line": text, "spoken": True,
                                                   "next": sheet.line_text(upcoming), "mode": perf.mode}))
                line_idx = -1                # the sung line comes back after it
                continue
            idx = max([i for i, ln in enumerate(lines) if ln[0].time - 0.6 <= now] or [0])
            nxt = lines[idx + 1] if idx + 1 < len(lines) else []
            if idx != line_idx:
                line_idx = idx
                self.bus.emit(Message(LYRICS_MSG, {"title": title, "line": sheet.line_text(lines[idx]),
                                                   "next": sheet.line_text(nxt), "mode": perf.mode}))
                if perf.mode == "sing" and perf.visemes and kind == "start":
                    # the mouth, after the first words: a screen that shows
                    # the face while the device sings (Emkay) does so on them
                    self.bus.emit(Message("enclosure.mouth.viseme_list",
                                          {"start": time.time() - now, "visemes": perf.visemes}))
            if show_sheet:
                self.gui["image"] = sheet.render(title, lines[idx], nxt, song.chords, now,
                                                 show_music=perf.mode == "karaoke")
                if not shown:
                    self.gui["title"] = ""
                    self.gui["caption"] = ""
                    self.gui["fill"] = "PreserveAspectFit"
                    self.gui["background_color"] = sheet.BG
                    self.gui.show_page("SYSTEM_ImageFrame", override_idle=True)
                    shown = True
        if not run.wait(max(0.0, song.length - (time.monotonic() - t0))):
            self._finish(run, perf.mode)

    def _finish(self, run, mode="karaoke"):
        with self._lock:
            if self._run is not run:
                return
            self._run = None
        run.set()
        self.bus.emit(Message(ENDED_MSG))
        self.gui.release()
        if mode == "sing":            # as Mycroft did: "thank you for listening", "now you try"
            self._last_sung = time.time()
            self.speak_dialog("after_song")

    def stop(self):
        with self._lock:
            run, self._run = self._run, None
        if run is None:
            return False
        run.set()
        self.bus.emit(Message("ovos.midi.stop", {"source": self.skill_id}))
        self.bus.emit(Message(ENDED_MSG))
        self.gui.release()
        return True
