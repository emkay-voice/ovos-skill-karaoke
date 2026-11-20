from ovos_skill_karaoke.songbook import Entry, find, local_songs, online_songs, short_title


def test_shipped_songs_listed():
    titles = {e.title for e in local_songs([])}
    assert {"Daisy Bell (Bicycle Built for Two)", "Hey Emkay (Disco)"} <= titles


def test_forgiving_match():
    e = local_songs([]) + [Entry("Jingle Bells", "online")]
    for q, want in [("daisy", "Daisy Bell"), ("bicycle built for two", "Daisy Bell"),
                    ("the disco song", "Hey Emkay"), ("jinglebells", "Jingle Bells")]:
        assert short_title(find(q, e).title) == want
    assert find("mester jakob", e) is None


def test_own_songs_come_first(tmp_path, kar):
    (tmp_path / "my_daisy.kar").write_bytes(kar("daisy_bell"))
    e = local_songs([tmp_path])
    assert e[0].where == "own"


def test_online_index():
    def fetch(url):
        assert url == "https://example.org/songbook/index.json"
        return b'{"songs": [{"file": "a.kar", "title": "A Song"}]}'
    e = online_songs("https://example.org/songbook", fetch)
    assert e[0].url == "https://example.org/songbook/a.kar" and e[0].where == "online"
    assert online_songs("https://x", lambda u: (_ for _ in ()).throw(OSError())) == []
