"""The public page: nothing of the family's gets out."""
import json

import public


def test_only_the_screen_is_allowed():
    for ok in ("/", "/state", "/state?x=1", "/weekend", "/track/9/2026", "/h2h?a=1&b=16", "/static/logo.svg"):
        assert public.allowed(ok), ok
    for no in ("/predictions", "/history/driver?code=VER", "/briefing", "/radio?p=x", "/login",
               "/ai/ask", "/share/prepare", "/stateful", "/weekend/x"):
        assert not public.allowed(no), no


def test_no_family_in_the_public_screen(new_race):
    from conftest import play
    import config
    r = play(new_race, "demo")
    v = public.sanitise(r.view(), r)
    text = json.dumps(v)
    for person in config.PEOPLE:
        assert person not in text, person
    assert v["viewer"] == "public" and v["people"] == {}
    assert all(not row["watchers"] for row in v["rows"])
    assert all(not e["who"] and "audio" not in e for e in v["events"])
    assert len(v["pitWall"]) > 5                              # everyone, so visitors can follow anyone
    assert len(v["gaps"]) > 5
    fin = [e for e in v["events"] if e["kind"] == "finish"]
    assert fin and fin[0]["text"].endswith("wins.")


def test_cache_gives_compressed_copy(new_race):
    import gzip
    from conftest import play
    c = public.Cache(play(new_race, "demo"))
    plain, packed = c.get()
    assert gzip.decompress(packed) == plain and len(packed) < len(plain) / 3
