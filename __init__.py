"""ovos-skill-karaoke: OVOS sings - or you do.

  "sing Daisy Bell"       the device sings it with its own voice, autotuned
                          onto the melody, with a band behind it
  "karaoke Jingle Bells"  the band plays (with the melody as a guide) and the
                          screen shows the words, the chords and the melody
  "what songs can you sing"

Songs are MIDI-karaoke (.kar) files: a few ship with the skill, more come
from the online songbook, and your own go in the skill's `songs` folder.
Everything runs on the device in Python (numpy): the voice is the device's
own TTS, the band a small General MIDI synthesizer (or FluidSynth, if
installed), so it works on any OVOS - a Mark II, a Raspberry Pi, a PC,
Docker, or Emkay in a browser.

The pieces: song.py (reading .kar), nuclei.py + singer.py + dsp.py (the
singing voice), band.py (the band), stage.py (mixing and the cache),
sheet.py (the screen), songbook.py (finding songs), voice.py (the TTS).
"""
from __future__ import annotations

import random
import threading
import time
from pathlib import Path

from ovos_bus_client.message import Message
from ovos_utils.ocp import MediaEntry, MediaType, PlaybackType
from ovos_workshop.decorators import intent_handler
from ovos_workshop.skills import OVOSSkill

from . import sheet, songbook, stage
from .singer import Singer
from .voice import device_voice, voice_id

SKILL_ROOT = Path(__file__).resolve().parent
SONGBOOK_URL = "https://raw.githubusercontent.com/emkay-voice/ovos-skill-karaoke/main/songbook/"
#: hosts may show the words, e.g. under the face while the device sings
LYRICS_MSG = "ovos.karaoke.lyrics"
ENDED_MSG = "ovos.karaoke.ended"
START_WAIT = 3.0          # seconds to wait for the player to say it started


class KaraokeSkill(OVOSSkill):

    def initialize(self):
        self.settings.setdefault("songbook_url", SONGBOOK_URL)
        self.settings.setdefault("song_folders", [])
        self.settings.setdefault("soundfont", "")
        self.settings.setdefault("sing_screen", "face")    # or "lyrics"
        self._singers = {}
        self._run = None          # threading.Event of the running performance
        self._lock = threading.Lock()
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
            known = {songbook.normalise(e.title) for e in found}
            found += [e for e in songbook.online_songs(self.settings.get("songbook_url"), self._fetch)
                      if songbook.normalise(e.title) not in known]
        return found

    def _find(self, name: str):
        local = self._entries(online=False)
        return songbook.find(name, local) or songbook.find(name, self._entries(online=True))

    def _song_bytes(self, entry) -> bytes:
        if entry.path:
            return entry.path.read_bytes()
        cached = Path(self.file_system.path) / "online" / entry.url.rsplit("/", 1)[-1]
        if not cached.exists():
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(self._fetch(entry.url))
        return cached.read_bytes()

    def _pick_any(self):
        entries = self._entries(online=False) or self._entries(online=True)
        return random.choice(entries) if entries else None

    # ------------------------------------------------------------ intents
    @intent_handler("sing.intent")
    def handle_sing(self, message):
        self._start(message.data.get("song", ""), "sing")

    @intent_handler("sing_any.intent")
    def handle_sing_any(self, message):
        self._start("", "sing")

    @intent_handler("karaoke.intent")
    def handle_karaoke(self, message):
        self._start(message.data.get("song", ""), "karaoke")

    @intent_handler("karaoke_any.intent")
    def handle_karaoke_any(self, message):
        self._start("", "karaoke")

    @intent_handler("list_songs.intent")
    def handle_list(self, message):
        titles = sorted({songbook.short_title(e.title) for e in self._entries()})
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
    def _start(self, name: str, mode: str):
        if not songbook.normalise(name):          # "sing a song" read as song="a song"
            name = ""
        entry = self._find(name) if name else self._pick_any()
        if entry is None:
            local = sorted({songbook.short_title(e.title) for e in self._entries()})
            self.speak_dialog("unknown_song", {"song": name, "songs": self._join(local[:5])})
            return
        self.stop()
        title = songbook.short_title(entry.title)
        self.speak_dialog("warming_up" if mode == "sing" else "setting_up", {"song": title}, wait=True)
        try:
            perf = self._prepare(entry, mode)
        except Exception as e:
            self.log.exception(f"could not prepare {entry.title}: {e}")
            self.speak_dialog("no_voice" if mode == "sing" else "no_band")
            return
        self._perform(perf, title)

    def _singer(self) -> Singer:
        vid = voice_id(self.lang)
        if vid not in self._singers:      # keeps its spoken words between songs
            self._singers[vid] = Singer(device_voice(self.bus, self.lang), self.lang)
        return self._singers[vid]

    def _prepare(self, entry, mode):
        data = self._song_bytes(entry)
        singer = self._singer() if mode == "sing" else None
        return stage.prepare(data, mode, Path(self.file_system.path) / "cache", singer=singer,
                             voice_id=voice_id(self.lang) if singer else "",
                             soundfont=self.settings.get("soundfont", ""), title=entry.title)

    def _perform(self, perf, title):
        run = threading.Event()
        with self._lock:
            self._run = run
        started = threading.Event()

        def on_start(_):
            started.set()
        for ev in ("mycroft.audio.playing_track", "ovos.common_play.track.state",
                   "ovos.audio.browser.state"):
            self.bus.once(ev, on_start)

        entry = MediaEntry(uri=f"file://{perf.path}", title=title, artist="Karaoke",
                           media_type=MediaType.MUSIC, playback=PlaybackType.AUDIO,
                           skill_id=self.skill_id, skill_icon=str(SKILL_ROOT / "icon.png"),
                           image=str(SKILL_ROOT / "icon.png"), length=int(perf.song.length * 1000))
        self.audio_service.play([entry])
        started.wait(START_WAIT)
        t0 = time.monotonic() - 0.15          # the player answers a moment after it starts
        if perf.mode == "sing" and perf.visemes:
            self.bus.emit(Message("enclosure.mouth.viseme_list",
                                  {"start": time.time() - 0.15, "visemes": perf.visemes}))
        threading.Thread(target=self._follow, args=(perf, title, t0, run), daemon=True).start()

    def _follow(self, perf, title, t0, run):
        """Keep the screen (and the words on the bus) in time with the song."""
        song, lines = perf.song, perf.song.lines
        show_sheet = perf.mode == "karaoke" or self.settings.get("sing_screen") == "lyrics"
        times = sorted({s.time for s in song.syllables})
        line_idx = -1
        shown = False
        # the first line at once (read along during the intro), then at every
        # syllable: the line moves on just before its first word
        for t in [0.0] + times:
            delay = t - (time.monotonic() - t0)
            if run.wait(max(0.0, delay)):
                return
            now = time.monotonic() - t0
            idx = max([i for i, ln in enumerate(lines) if ln[0].time - 0.6 <= now] or [0])
            nxt = lines[idx + 1] if idx + 1 < len(lines) else []
            if idx != line_idx:
                line_idx = idx
                self.bus.emit(Message(LYRICS_MSG, {"title": title, "line": sheet.line_text(lines[idx]),
                                                   "next": sheet.line_text(nxt), "mode": perf.mode}))
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
        run.wait(max(0.0, song.length - (time.monotonic() - t0)))
        self._finish(run)

    def _finish(self, run):
        with self._lock:
            if self._run is not run:
                return
            self._run = None
        run.set()
        self.bus.emit(Message(ENDED_MSG))
        self.gui.release()

    def stop(self):
        with self._lock:
            run, self._run = self._run, None
        if run is None:
            return False
        run.set()
        self.audio_service.stop()
        self.bus.emit(Message(ENDED_MSG))
        self.gui.release()
        return True
