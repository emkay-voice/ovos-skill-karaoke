from ovos_skill_karaoke.songbook import Entry, find, local_songs, online_songs, search, short_title


def test_shipped_songs_listed():
    titles = {e.title for e in local_songs([])}
    assert {"Daisy Bell (Bicycle Built for Two)", "Hey Emkay (Disco)"} <= titles


def test_forgiving_match():
    e = local_songs([]) + [Entry("Jingle Bells", "online")]
    for q, want in [("daisy", "Daisy Bell"), ("bicycle built for two", "Daisy Bell"),
                    ("the disco song", "Hey Emkay"), ("jinglebells", "Jingle Bells")]:
        assert short_title(find(q, e).title) == want
    assert find("yellow submarine", e) is None


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


def test_languages_are_one_song():
    e = local_songs([])
    assert find("hej emkay", e, "da-dk").path.name == "hey_emkay.da.kar"
    assert find("hey emkay", e, "da-dk").path.name == "hey_emkay.da.kar"
    assert find("hey emkay", e, "en-us").path.name == "hey_emkay.kar"
    assert find("hey emkay", e, "ja-jp").path.name == "hey_emkay.kar"     # no Japanese: English


def test_search_by_tag_and_words():
    e = local_songs([])
    assert short_title(search("something for bedtime", e, "en")[0].title) == "Goodnight from Emkay"
    assert short_title(search("a song about the moon", e, "en")[0].title) == "Goodnight from Emkay"
    assert "Hej Emkay" in [short_title(x.title) for x in search("noget til fest", e, "da")]


def test_frere_jacques_in_every_language():
    e = local_songs([])
    for lang, title in [("da-dk", "Mester Jakob"), ("de-de", "Bruder Jakob"), ("it-it", "Fra Martino"),
                        ("fr-fr", "Frère Jacques"), ("en-us", "Are You Sleeping (Frère Jacques)")]:
        assert find("frere jacques", e, lang).title == title
