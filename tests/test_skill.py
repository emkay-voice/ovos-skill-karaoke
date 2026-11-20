import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import ovos_skill_karaoke as sk
from ovos_skill_karaoke.singer import Singer

ROOT = Path(sk.__file__).resolve().parent


@pytest.fixture
def skill(tmp_path, monkeypatch, speak):
    monkeypatch.setattr(sk, "START_WAIT", 0.01)
    s = sk.KaraokeSkill.__new__(sk.KaraokeSkill)
    s.log = MagicMock()
    s.skill_id = "ovos-skill-karaoke.test"
    monkeypatch.setattr(sk.KaraokeSkill, "lang", "en-us", raising=False)
    monkeypatch.setattr(sk.KaraokeSkill, "file_system", MagicMock(path=str(tmp_path)), raising=False)
    monkeypatch.setattr(sk.KaraokeSkill, "bus", MagicMock(), raising=False)
    monkeypatch.setattr(sk.KaraokeSkill, "gui", MagicMock(), raising=False)
    monkeypatch.setattr(sk.KaraokeSkill, "settings", {"songbook_url": ""}, raising=False)
    monkeypatch.setattr(sk.KaraokeSkill, "dialog_renderer",
                        MagicMock(render=lambda name, data: "and"), raising=False)
    s.status = MagicMock()
    s.audio_service = MagicMock()
    s.get_response = MagicMock(return_value=None)
    s.ask_yesno = MagicMock(return_value=None)

    def voc_match(utt, name, lang=None, exact=False):
        words = (ROOT / "locale" / "en-US" / f"{name}.voc").read_text().splitlines()
        return any(w and w in (utt or "").lower() for w in words)
    s.voc_match = voc_match
    s.speak_dialog = MagicMock()
    s._singer = lambda: Singer(speak, "en")
    s.initialize()
    yield s
    s.stop()


def test_unknown_song(skill):
    skill.handle_sing(MagicMock(data={"song": "mester jakob"}))
    name, data = skill.speak_dialog.call_args[0]
    assert name == "unknown_song" and "Daisy Bell" in data["songs"]


def test_sings_a_song(skill):
    skill.handle_sing(MagicMock(data={"song": "the disco song"}))
    assert skill.speak_dialog.call_args_list[0][0][0] == "warming_up"
    entry = skill.audio_service.play.call_args[0][0][0]
    assert entry.uri.startswith("file://") and Path(entry.uri[7:]).stat().st_size > 100000
    assert entry.title == "Hey Emkay"
    sent = [c[0][0].msg_type for c in skill.bus.emit.call_args_list]
    assert "enclosure.mouth.viseme_list" in sent
    time.sleep(0.3)
    assert sk.LYRICS_MSG in [c[0][0].msg_type for c in skill.bus.emit.call_args_list]
    assert skill.stop() is True
    skill.audio_service.stop.assert_called_once()
    assert skill.stop() is False


def test_second_time_is_cached(skill):
    skill.handle_karaoke(MagicMock(data={"song": "daisy"}))
    first = skill.audio_service.play.call_args[0][0][0].uri
    skill.stop()
    t = time.monotonic()
    skill.handle_karaoke(MagicMock(data={"song": "daisy bell"}))
    assert skill.audio_service.play.call_args[0][0][0].uri == first
    assert time.monotonic() - t < 1.0


def test_karaoke_shows_the_sheet(skill):
    skill.handle_karaoke(MagicMock(data={"song": "daisy"}))
    time.sleep(0.3)
    skill.gui.show_page.assert_called_with("SYSTEM_ImageFrame", override_idle=True)


def test_a_song_read_as_a_name(skill):
    skill.handle_sing(MagicMock(data={"song": "a song"}))    # "sing a song"
    assert skill.audio_service.play.called


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


def test_shy_then_sings_when_encouraged(skill):
    skill.get_response.return_value = "oh come on"
    skill.handle_sing_any(MagicMock(data={}))
    assert skill.get_response.call_args[0][0] == "shy"
    assert [c[0][0] for c in skill.speak_dialog.call_args_list][:2] == ["okay_then", "warming_up"]
    assert skill.audio_service.play.called


def test_shy_and_stays_shy(skill):
    skill.get_response.return_value = "no it's fine"
    skill.handle_sing_any(MagicMock(data={}))
    skill.speak_dialog.assert_called_with("maybe_later")
    assert not skill.audio_service.play.called


def test_not_shy_right_after_singing(skill):
    skill._last_sung = time.time()
    skill.handle_sing_any(MagicMock(data={}))
    assert not skill.get_response.called and skill.audio_service.play.called


def test_find_a_song(skill):
    skill.ask_yesno.return_value = "yes"
    skill.handle_find(MagicMock(data={"query": "bedtime"}))
    assert skill.ask_yesno.call_args[0][1] == {"song": "Goodnight from Emkay"}
    skill.gui.show_page.assert_any_call("SYSTEM_grid", override_idle=60)
    assert skill.audio_service.play.call_args[0][0][0].title == "Goodnight from Emkay"


def test_find_many_and_pick_one(skill):
    skill.get_response.return_value = "the disco one"
    skill.handle_find(MagicMock(data={"query": "emkay"}))
    assert skill.get_response.call_args[0][0] == "found_many"
    assert skill.audio_service.play.call_args[0][0][0].title == "Hey Emkay"


def test_nothing_found(skill):
    skill.handle_find(MagicMock(data={"query": "submarines"}))
    skill.speak_dialog.assert_called_with("no_match", {"query": "submarines"})
