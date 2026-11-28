import base64
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ovos_bus_client.message import Message

import ovos_skill_karaoke as sk
from ovos_skill_karaoke.singer import Singer

ROOT = Path(sk.__file__).resolve().parent


@pytest.fixture
def skill(tmp_path, monkeypatch, speak):
    monkeypatch.setattr(sk, "START_AFTER", 0.05)
    s = sk.KaraokeSkill.__new__(sk.KaraokeSkill)
    monkeypatch.setattr(sk.OVOSSkill, "__init__", lambda self, *a, **k: None)
    sk.KaraokeSkill.__init__(s)
    s.log = MagicMock()
    s.skill_id = "ovos-skill-karaoke.test"
    monkeypatch.setattr(sk.KaraokeSkill, "lang", "en-us", raising=False)
    monkeypatch.setattr(sk.KaraokeSkill, "file_system", MagicMock(path=str(tmp_path)), raising=False)
    bus = MagicMock()
    bus.band = {"engine": "builtin", "latency": 0.05, "clips": True}   # ovos-phal-plugin-midi's answer

    def answer(msg, reply, timeout=None):
        if msg.msg_type == "ovos.midi.status":
            return Message(reply, bus.band) if bus.band else None
        return Message(reply, {"at": msg.data.get("at"), "duration": 1.0})
    bus.wait_for_response.side_effect = answer
    monkeypatch.setattr(sk.KaraokeSkill, "bus", bus, raising=False)
    monkeypatch.setattr(sk.KaraokeSkill, "gui", MagicMock(), raising=False)
    monkeypatch.setattr(sk.KaraokeSkill, "settings", {"songbook_url": ""}, raising=False)
    monkeypatch.setattr(sk.KaraokeSkill, "dialog_renderer",
                        MagicMock(render=lambda name, data: "and"), raising=False)
    s.status = MagicMock()
    s.get_response = MagicMock(return_value=None)
    s.ask_yesno = MagicMock(return_value=None)

    def voc_match(utt, name, lang=None, exact=False):
        words = (ROOT / "locale" / "en-US" / f"{name}.voc").read_text().splitlines()
        return any(w and w in (utt or "").lower() for w in words)
    s.voc_match = voc_match
    s.speak_dialog = MagicMock()
    s.add_event = MagicMock()
    s._singer = lambda: Singer(speak, "en")
    s.initialize()
    yield s
    s.stop()


def sent(skill, msg_type):
    return [c[0][0].data for c in skill.bus.emit.call_args_list if c[0][0].msg_type == msg_type]


def played(skill):
    """What the band was given last (ovos.midi.play), or None."""
    p = sent(skill, "ovos.midi.play")
    return p[-1] if p else None


def title(skill):
    return [c[0][1]["song"] for c in skill.speak_dialog.call_args_list
            if c[0][0] in ("warming_up", "setting_up")][-1]


def test_asked_before_initialize(monkeypatch):
    """Its intents can be asked before initialize() has run (Emkay loads a
    skill for a minute): stop() and the state must be there already."""
    monkeypatch.setattr(sk.OVOSSkill, "__init__", lambda self, *a, **k: None)
    s = sk.KaraokeSkill()
    assert s.stop() is False and s._last_sung == 0.0


def test_unknown_song(skill):
    skill.handle_sing(MagicMock(data={"song": "yellow submarine"}))
    name, data = skill.speak_dialog.call_args[0]
    assert name == "unknown_song" and "Daisy Bell" in data["songs"]


def test_sings_a_song(skill):
    skill.handle_sing(MagicMock(data={"song": "the disco song"}))
    assert skill.speak_dialog.call_args_list[0][0][0] == "warming_up"
    assert title(skill) == "Hey Emkay"
    play = played(skill)
    clip = [c[0][0].data for c in skill.bus.wait_for_response.call_args_list
            if c[0][0].msg_type == "ovos.midi.clip"][-1]
    assert play["at"] == clip["at"] and play["source"] == clip["source"] == skill.skill_id
    assert Path(clip["path"]).stat().st_size > 100000                # the sung voice
    assert play["mute"] and base64.b64decode(play["data"])[:4] == b"MThd"
    time.sleep(0.4)
    assert sk.LYRICS_MSG in [c[0][0].msg_type for c in skill.bus.emit.call_args_list]
    assert sent(skill, "enclosure.mouth.viseme_list")
    assert skill.stop() is True
    assert sent(skill, "ovos.midi.stop") == [{"source": skill.skill_id}]
    assert skill.stop() is False


def test_karaoke_plays_the_whole_band(skill):
    skill.handle_karaoke(MagicMock(data={"song": "daisy"}))
    play = played(skill)
    assert play["mute"] == [] and play["transpose"] == 0
    assert not [c for c in skill.bus.wait_for_response.call_args_list if c[0][0].msg_type == "ovos.midi.clip"]


def test_the_voice_is_kept(skill):
    skill.handle_sing(MagicMock(data={"song": "daisy"}))
    skill.stop()
    t = time.monotonic()
    skill.handle_sing(MagicMock(data={"song": "daisy bell"}))
    assert time.monotonic() - t < 1.0


def test_no_band_no_song(skill):
    """Without ovos-phal-plugin-midi answering, it says so and plays nothing."""
    skill.bus.band = None
    skill.handle_sing(MagicMock(data={"song": "daisy"}))
    skill.speak_dialog.assert_called_with("no_midi")
    assert played(skill) is None


def test_voice_sent_itself_when_the_plugin_cannot_read_it(skill):
    def answer(msg, reply, timeout=None):
        if msg.msg_type == "ovos.midi.status":
            return Message(reply, skill.bus.band)
        return Message(reply, {"error": "No such file"})
    skill.bus.wait_for_response.side_effect = answer
    skill.handle_sing(MagicMock(data={"song": "daisy"}))
    clip = sent(skill, "ovos.midi.clip")[-1]
    assert "path" not in clip and base64.b64decode(clip["data"])[:4] == b"RIFF"


def test_karaoke_shows_the_sheet(skill):
    skill.handle_karaoke(MagicMock(data={"song": "daisy"}))
    time.sleep(0.4)
    skill.gui.show_page.assert_called_with("SYSTEM_ImageFrame", override_idle=True)


def test_a_song_read_as_a_name_is_the_shy_question(skill):
    """"can you sing a song?" matches "can you sing {song}" with song "a
    song": that is no title, so it gets shy as "sing_any" does."""
    skill.get_response.return_value = "come on"
    skill.handle_sing(MagicMock(data={"song": "a song"}))
    assert skill.get_response.call_args[0][0] == "shy"
    assert played(skill)


def test_locales_match():
    """Every Emkay language has every file, and the slots are kept."""
    langs = sorted(p.name for p in (ROOT / "locale").iterdir())
    assert {"en-US", "da-DK", "de-DE", "es-ES", "fr-FR", "it-IT", "nl-NL", "pt-PT", "pt-BR",
            "ca-ES", "eu-ES", "gl-ES"} <= set(langs)
    files = {p.name for p in (ROOT / "locale" / "en-US").iterdir()}
    for lang in langs:
        assert {p.name for p in (ROOT / "locale" / lang).iterdir()} == files, lang
        for name, slot in (("sing.intent", "{song}"), ("karaoke.intent", "{song}"), ("find_song.intent", "{query}")):
            lines = [ln for ln in (ROOT / "locale" / lang / name).read_text().splitlines() if ln]
            assert lines and all(slot in ln for ln in lines), (lang, name)


def test_shy_then_sings_when_encouraged(skill, monkeypatch):
    """With no song that gives in by itself: "here goes nothing", as Mycroft."""
    pick = skill._pick_any
    monkeypatch.setattr(skill, "_pick_any", lambda tag="": None if tag else pick())
    skill.get_response.return_value = "oh come on"
    skill.handle_sing_any(MagicMock(data={}))
    assert skill.get_response.call_args[0][0] == "shy"
    assert [c[0][0] for c in skill.speak_dialog.call_args_list][:2] == ["okay_then", "warming_up"]
    assert played(skill)


def test_shy_and_stays_shy(skill):
    skill.get_response.return_value = "no it's fine"
    skill.handle_sing_any(MagicMock(data={}))
    skill.speak_dialog.assert_called_with("maybe_later")
    assert played(skill) is None


def test_not_shy_right_after_singing(skill):
    skill._last_sung = time.time()
    skill.handle_sing_any(MagicMock(data={}))
    assert not skill.get_response.called and played(skill)


def test_find_a_song(skill):
    skill.ask_yesno.return_value = "yes"
    skill.handle_find(MagicMock(data={"query": "lullaby"}))
    assert skill.ask_yesno.call_args[0][1] == {"song": "Goodnight from Emkay"}
    skill.gui.show_page.assert_any_call("SYSTEM_grid", override_idle=60)
    assert title(skill) == "Goodnight from Emkay"


def test_find_many_and_pick_one(skill):
    skill.get_response.return_value = "the disco one"
    skill.handle_find(MagicMock(data={"query": "emkay"}))
    assert skill.get_response.call_args[0][0] == "found_many"
    assert title(skill) == "Hey Emkay"


def test_nothing_found(skill):
    skill.handle_find(MagicMock(data={"query": "submarines"}))
    skill.speak_dialog.assert_called_with("no_match", {"query": "submarines"})


def test_coaxed_it_sings_the_song_that_gives_in(skill, monkeypatch):
    """"come on" - and Emkay's Swing starts by giving in itself ("Okay...
    okay, I'll sing"), so no "here goes nothing" first."""
    skill.get_response.return_value = "oh come on"
    skill.handle_sing_any(MagicMock(data={}))
    spoken = [c[0][0] for c in skill.speak_dialog.call_args_list]
    assert "okay_then" not in spoken
    assert title(skill) == "Emkay's Swing"
    clip = [c[0][0].data for c in skill.bus.wait_for_response.call_args_list
            if c[0][0].msg_type == "ovos.midi.clip"][-1]
    assert Path(clip["path"]).exists()


def test_another_skill_asks_for_a_song(skill, monkeypatch):
    started = []
    monkeypatch.setattr(skill, "_start", lambda name, mode, **k: started.append((name, mode)))
    handler = {c[0][0]: c[0][1] for c in skill.add_event.call_args_list}["ovos.karaoke.sing"]
    handler(Message("ovos.karaoke.sing", {"song": "daisy bell"}))
    handler(Message("ovos.karaoke.sing", {"mode": "karaoke"}))
    assert started == [("daisy bell", "sing"), ("", "karaoke")]
