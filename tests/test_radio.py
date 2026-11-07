"""Team radio clip addresses."""
import radio


def test_accents_in_the_folder_name_are_written_the_web_way():
    url = radio.clip_url("2026/2026-11-08_São_Paulo_Grand_Prix/2026-11-07_Sprint/",
                         "TeamRadio/MAXVER01_1_20261107_143322.mp3")
    assert url == ("https://livetiming.formula1.com/static/2026/2026-11-08_S%C3%A3o_Paulo_Grand_Prix/"
                   "2026-11-07_Sprint/TeamRadio/MAXVER01_1_20261107_143322.mp3")
    url.encode("ascii")      # nothing left that a web request can't send


def test_plain_names_are_unchanged():
    assert radio.clip_url("2026/2026-10-25_United_States_Grand_Prix/2026-10-25_Race/", "TeamRadio/a.mp3") == \
        "https://livetiming.formula1.com/static/2026/2026-10-25_United_States_Grand_Prix/2026-10-25_Race/TeamRadio/a.mp3"
