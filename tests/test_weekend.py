"""The between-sessions screen: which session is next, and what's done."""
from datetime import datetime, timezone

import weekend

RACES = [{"season": "2026", "round": "18", "raceName": "United States Grand Prix",
          "Circuit": {"circuitName": "Circuit of the Americas",
                      "Location": {"lat": "30.1328", "long": "-97.6411", "locality": "Austin", "country": "USA"}},
          "date": "2026-10-25", "time": "20:00:00Z",
          "FirstPractice": {"date": "2026-10-23", "time": "17:30:00Z"},
          "SecondPractice": {"date": "2026-10-23", "time": "21:00:00Z"},
          "ThirdPractice": {"date": "2026-10-24", "time": "17:30:00Z"},
          "Qualifying": {"date": "2026-10-24", "time": "21:00:00Z"}},
         {"season": "2026", "round": "19", "raceName": "Mexico City Grand Prix",
          "Circuit": {"circuitName": "Autodromo Hermanos Rodriguez",
                      "Location": {"lat": "19.4", "long": "-99.09", "locality": "Mexico City", "country": "Mexico"}},
          "date": "2026-11-01", "time": "20:00:00Z", "Qualifying": {"date": "2026-10-31", "time": "21:00:00Z"}}]


def view_at(monkeypatch, when):
    monkeypatch.setattr(weekend.history, "_get", lambda path, ttl, **k: {"MRData": {"RaceTable": {"Races": RACES}}})
    monkeypatch.setattr(weekend, "outlook", lambda lat, lon, at: {"temp": 24.0, "rain": 10, "wind": 6.0})
    return weekend.view(datetime(*when, tzinfo=timezone.utc))


def test_saturday_lunchtime_qualifying_is_next(monkeypatch):
    v = view_at(monkeypatch, (2026, 10, 24, 12, 0))
    assert v["name"] == "United States Grand Prix"
    assert v["next"]["name"] == "Practice 3" and not v["next"]["live"]
    assert [s["status"] for s in v["sessions"]] == ["done", "done", "next", "next", "next"]
    assert v["outlook"]["rain"] == 10


def test_during_qualifying_it_says_live(monkeypatch):
    v = view_at(monkeypatch, (2026, 10, 24, 21, 20))
    assert v["next"]["name"] == "Qualifying" and v["next"]["live"]


def test_after_the_race_it_moves_on_to_the_next_weekend(monkeypatch):
    v = view_at(monkeypatch, (2026, 10, 26, 9, 0))
    assert v["name"] == "Mexico City Grand Prix" and v["next"]["name"] == "Qualifying"
