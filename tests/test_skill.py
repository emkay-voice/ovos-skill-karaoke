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
    files = {p.name for p in (ROOT / "locale" / "en-us").iterdir()}
    assert files == {p.name for p in (ROOT / "locale" / "da-dk").iterdir()}
    for name in ("sing.intent", "karaoke.intent"):
        for lang in ("en-us", "da-dk"):
            assert all("{song}" in ln for ln in (ROOT / "locale" / lang / name).read_text().splitlines() if ln)
