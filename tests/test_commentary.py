"""AI commentary: the facts it's given, which topic it picks, and when it speaks."""
import os
import time

import commentary
import notify
from conftest import ROOT, play


def play_until(race, folder, lap):
    from feed import REPLAY_TOPICS, _parse_stream
    entries = []
    for t in REPLAY_TOPICS:
        f = os.path.join(ROOT, folder, t + ".jsonStream")
        if os.path.exists(f):
            with open(f, encoding="utf-8-sig") as fh:
                entries += _parse_stream(fh.read(), t)
    entries.sort(key=lambda e: e[0])
    for t, topic, data in entries:
        race.set_clock(t, 1.0)
        race.update(topic, data)
        if race.lap()[0] and int(race.lap()[0]) >= lap:
            break
    return race


def test_topics_match_the_session(new_race):
    play(new_race, "demo-quali")
    assert [t for t, _ in commentary.TOPICS[commentary.kind(new_race)]] == ["summary", "cut", "weather"]


def test_race_facts_come_from_the_timing(new_race):
    play_until(new_race, "demo", 16)
    assert new_race.grid                                   # starting grid taken at lights out
    summary = commentary.facts(new_race, "summary")
    assert "lap 16 of 20" in summary and "started P" in summary
    tyres = commentary.facts(new_race, "tyres")
    assert "laps old" in tyres and "followed by" in tyres
    catching = commentary.facts(new_race, "catching")
    assert "closing" in catching and "a lap" in catching
    sc = commentary.facts(new_race, "safetycar")
    assert "under a safety car about 11" in sc            # Austin: 20 s, roughly halved
    assert "would drop them to about P" in sc


def test_pit_stops_dont_count_as_catching(new_race):
    play_until(new_race, "demo", 13)
    for line in commentary.facts(new_race, "catching").splitlines():
        assert "0.0s behind" not in line


def test_never_the_same_topic_twice_running(new_race):
    play_until(new_race, "demo", 16)
    first = commentary.pick(new_race)
    assert commentary.pick(new_race, last=first) != first


def test_commentator_speaks_every_five_laps_and_not_under_a_safety_car(new_race, monkeypatch):
    monkeypatch.setattr(notify.config, "ANTHROPIC_API_KEY", "sk-ant-test")
    new_race.notify.enabled = True
    c = commentary.Commentator.__new__(commentary.Commentator)
    c.race, c.on, c.last_topic, c.last_time, c.last_lap = new_race, True, None, 0.0, None
    play_until(new_race, "demo", 4)
    assert c.due()
    c.last_time, c.last_lap = time.time() - 1000, 4
    play_until(new_race, "demo", 6)
    assert not c.due()                                     # only two laps since
    new_race.track = "4"                                   # safety car out
    c.last_lap = 0
    assert not c.due()
    new_race.track = "1"
    assert c.due()
    c.on = False
    assert not c.due()


def test_read_sends_the_facts_and_returns_the_words(new_race, monkeypatch):
    play_until(new_race, "demo", 16)
    seen = {}

    def fake_ask(prompt, max_tokens=1000, **k):
        seen["prompt"] = prompt
        return "Stroll is closing on Antonelli."
    monkeypatch.setattr(notify, "ask_claude", fake_ask)
    topic, text = commentary.read(new_race, "catching")
    assert topic == "catching" and text.startswith("Stroll")
    assert "Topic: Who's catching who." in seen["prompt"]
    assert "don't invent tyre wear" in seen["prompt"]
    assert "James follows" in seen["prompt"]


def test_pit_stops_show_who_won_and_lost(new_race):
    play_until(new_race, "demo", 14)
    assert len(new_race.pit_log) >= 5                      # Hamilton early, then five under the safety car
    ham = [s for s in new_race.pit_log if s["num"] == "44"][0]
    assert ham["lap"] and ham["pos"] and ham["tyre"]
    text = commentary.facts(new_race, "pits")
    assert "Hamilton" in text and "pitted on lap" in text and "Still to stop:" in text


def test_weather_only_picked_when_it_matters(new_race, monkeypatch):
    play_until(new_race, "demo", 16)
    assert commentary.weather_story(new_race)[1] < 2          # dry, steady: not worth a comment
    assert "weather" not in [commentary.pick(new_race, last=t) for t in ("summary", "tyres", "catching")]
    monkeypatch.setattr(commentary, "forecast", lambda race: {"chance": 80, "mm": 2.4})
    assert commentary.pick(new_race) == "weather"
    text = commentary.facts(new_race, "weather")
    assert "80% chance of rain" in text and "as at the start" in text


def test_rain_starting_is_the_story(new_race):
    play_until(new_race, "demo", 10)
    new_race.update("WeatherData", {"Rainfall": "1"})
    assert commentary.weather_story(new_race)[1] >= 3
    assert "raining at the track" in commentary.facts(new_race, "weather")


def test_rain_forecast_lookup_builds_its_address(new_race, monkeypatch):
    """The real forecast lookup (other tests skip it): check the address it asks for."""
    import importlib
    import io
    import json
    import urllib.request
    real = importlib.reload(commentary)              # undo the test-wide stand-in
    play_until(new_race, "demo", 3)
    asked = {}

    class Reply(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        asked["url"] = req.full_url
        return Reply(json.dumps({"minutely_15": {"precipitation": [0, 0.2, 0.4, 0]},
                                 "hourly": {"precipitation_probability": [30, 55]}}).encode())
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    real._forecast_cache.update(at=0.0, key=None, data=None)
    f = real.forecast(new_race)
    assert "latitude=30.1328&longitude=-97.6411" in asked["url"]
    assert f == {"chance": 55, "mm": 0.6}
