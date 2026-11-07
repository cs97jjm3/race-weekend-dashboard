"""The race morning briefing."""
from datetime import datetime, timezone

import briefing
import fia


def _weekend(now=None):
    return {"name": "São Paulo Grand Prix", "round": "21", "place": "São Paulo, Brazil",
            "sessions": [{"name": "Qualifying", "start": "2026-11-07T18:00:00+00:00", "status": "done"},
                         {"name": "Race", "start": "2026-11-08T17:00:00+00:00", "status": "next"}],
            "next": {"name": "Race", "start": "2026-11-08T17:00:00+00:00"},
            "outlook": {"temp": 24.4, "rain": 40, "wind": 9}}


def _quali(path, ttl):
    def r(pos, code, fam, team):
        return {"position": str(pos), "Driver": {"code": code, "familyName": fam}, "Constructor": {"name": team}}
    return {"MRData": {"RaceTable": {"Races": [{"QualifyingResults": [
        r(1, "NOR", "Norris", "McLaren"), r(2, "VER", "Verstappen", "Red Bull"), r(3, "LEC", "Leclerc", "Ferrari"),
        r(7, "HAM", "Hamilton", "Ferrari"), r(12, "ANT", "Antonelli", "Mercedes")]}]}}}


class FakeStandings:
    data = {"drivers": [{"name": "Norris", "points": 400.0}, {"name": "Piastri", "points": 388.0}]}


def test_race_morning(monkeypatch, tmp_path):
    monkeypatch.setattr(briefing.weekend, "view", _weekend)
    monkeypatch.setattr(briefing.history, "_get", _quali)
    monkeypatch.setattr(fia, "DOCS_FILE", str(tmp_path / "docs.json"))
    fia._keep_doc("São Paulo Grand Prix", "Infringement - Car 5", "u1",
                  {"No / Driver": "5 - Gabriel Bortoleto", "Decision": "Drop of 20 grid positions for the next Race in which the driver participates."})
    b = briefing.build(now=datetime(2026, 11, 8, 9, 30, tzinfo=timezone.utc), standings=FakeStandings())
    assert b["raceDay"] is True
    say = b["say"]
    assert say.startswith("Good morning. It's race day at the São Paulo Grand Prix.")
    assert "Norris is on pole, with Verstappen and Leclerc behind." in say
    assert "Hamilton 7th" in say and "Antonelli 12th" in say          # the family's drivers (config.example)
    assert "Bortoleto, 20 grid positions" in say
    assert "24 degrees, with a 40 percent chance of rain" in say
    assert "Norris leads Piastri by 12 points" in say


def test_day_before_is_not_race_day(monkeypatch, tmp_path):
    monkeypatch.setattr(briefing.weekend, "view", _weekend)
    monkeypatch.setattr(briefing.history, "_get", _quali)
    monkeypatch.setattr(fia, "DOCS_FILE", str(tmp_path / "docs.json"))
    b = briefing.build(now=datetime(2026, 11, 7, 20, 0, tzinfo=timezone.utc))
    assert b["raceDay"] is False and "is on Sunday at" in b["say"]


def test_nice_times():
    assert briefing.ordinal(1) == "1st" and briefing.ordinal(12) == "12th" and briefing.ordinal(22) == "22nd"
