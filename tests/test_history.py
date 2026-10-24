"""Driver, team and race history, using made-up Jolpica answers instead of the internet."""
import pytest

import history


def races(*items, total=None):
    return {"MRData": {"limit": "100", "offset": "0", "total": str(total if total is not None else len(items)),
                       "RaceTable": {"Races": list(items)}}}


def result(pos, code="VER", did="max_verstappen", team="red_bull", grid=1, status="Finished", text=None, pts=0):
    return {"position": str(pos), "positionText": text or str(pos), "grid": str(grid), "status": status,
            "points": str(pts), "laps": "56", "Time": {"time": "1:33:52.123"},
            "Driver": {"driverId": did, "code": code, "givenName": "Max", "familyName": "Verstappen"},
            "Constructor": {"constructorId": team, "name": "Red Bull"}}


@pytest.fixture
def jolpica(monkeypatch, tmp_path):
    """Answer Jolpica requests from a dict of path -> answer, and count the calls."""
    answers, calls = {}, []
    monkeypatch.setattr(history, "CACHE", str(tmp_path))

    def fake_get(path, ttl, **params):
        calls.append(path)
        if path not in answers:
            raise AssertionError("unexpected lookup " + path)
        return answers[path]
    monkeypatch.setattr(history, "_get", fake_get)
    return answers, calls


def test_finishing_labels():
    assert history._short(result(1)) == "P1"
    assert history._short(result(12, status="Lapped")) == "P12"
    assert history._short(result(18, text="R", status="Engine")) == "DNF"
    assert history._short(result(20, text="D", status="Disqualified")) == "DSQ"
    assert history._short(result(20, text="W", status="Withdrew")) == "DNS"


def test_built_in_champions():
    assert sum(1 for d in history.CHAMPIONS.values() if d == "hamilton") == 7
    assert sum(1 for d in history.CHAMPIONS.values() if d == "max_verstappen") == 4
    assert sum(1 for t in history.TEAM_CHAMPIONS.values() if t == "ferrari") == 16


def test_one_race_in_full(jolpica):
    answers, _ = jolpica
    r = {"season": "2025", "round": "19", "raceName": "United States Grand Prix", "date": "2025-10-19",
         "Circuit": {"circuitName": "Circuit of the Americas", "Location": {"locality": "Austin", "country": "USA"}},
         "Results": [result(1, grid=7, pts=25),
                     dict(result(2, code="LEC", did="leclerc", grid=1, pts=18), FastestLap={"rank": "1", "Time": {"time": "1:37.402"}}),
                     result(3, code="HAM", did="hamilton", grid=2, text="R", status="Engine")]}
    answers["2025/19/results.json"] = races(r)
    answers["2025/19/qualifying.json"] = races({"season": "2025", "round": "19", "QualifyingResults": [
        {"position": "1", "Driver": {"code": "LEC", "familyName": "Leclerc"}, "Constructor": {"name": "Ferrari"},
         "Q1": "1:34.1", "Q2": "1:33.9", "Q3": "1:33.5"}]})
    d = history.race(2025, 19)
    assert d["name"] == "United States Grand Prix" and d["place"] == "Austin, USA"
    assert d["results"][0]["gained"] == 6
    assert d["gainer"]["code"] == "VER"
    assert d["fastest"]["code"] == "LEC"
    assert d["retired"] == 1
    assert d["quali"][0]["q3"] == "1:33.5"


def test_paging_joins_a_race_split_across_pages(monkeypatch):
    pages = {0: {"MRData": {"limit": "2", "offset": "0", "total": "3", "RaceTable": {"Races": [
                {"season": "2026", "round": "1", "Results": [result(1), result(2)]}]}}},
             2: {"MRData": {"limit": "2", "offset": "2", "total": "3", "RaceTable": {"Races": [
                {"season": "2026", "round": "1", "Results": [result(3)]}]}}}}
    monkeypatch.setattr(history, "_get", lambda path, ttl, offset=0, **k: pages[offset])
    out = history._all_races("current/constructors/red_bull/results.json", 0)
    assert len(out) == 1 and len(out[0]["Results"]) == 3


def test_busy_jolpica_falls_back_to_saved_answers(monkeypatch, tmp_path):
    monkeypatch.setattr(history, "CACHE", str(tmp_path))
    monkeypatch.setattr(history, "_resting", [9e18])          # pretend Jolpica told us to wait
    with pytest.raises(history.TooBusy):
        history._get("current/drivers.json", 3600)
    assert "try new ones again" in str(history.TooBusy())
