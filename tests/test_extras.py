"""The session clock, pit stop times and the lap chart."""
import time

from conftest import play
from race import hms_seconds, parse_utc


def test_clock_text_is_read_as_seconds():
    assert hms_seconds("01:02:03") == 3723
    assert hms_seconds("12:34") == 754
    assert hms_seconds("") is None
    assert parse_utc("2026-10-24T21:00:00.1234567Z") == parse_utc("2026-10-24T21:00:00.123456Z")


def test_clock_counts_down_and_freezes_when_stopped(new_race):
    r = new_race
    r.set_clock(100, 1.0)
    r.update("ExtrapolatedClock", {"Utc": "2026-10-24T21:00:00Z", "Remaining": "00:10:00", "Extrapolating": True})
    r.set_clock(160, 1.0)
    assert round(r.clock_left()) == 540
    r.update("ExtrapolatedClock", {"Extrapolating": False})     # red flag: the clock stops
    r.set_clock(400, 1.0)
    assert round(r.clock_left()) == 540
    v = r.view()["clock"]
    assert v["running"] is False and round(v["left"]) == 540


def test_live_clock_allows_for_when_f1_stamped_it(new_race):
    r = new_race
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 30))
    r.update("ExtrapolatedClock", {"Utc": stamp, "Remaining": "00:10:00", "Extrapolating": True})
    assert 565 <= r.clock_left() <= 572


def _followed(race):
    return next(n for n in race.lines() if race.watchers(n))


def test_pit_stop_time_is_posted_once_for_followed_drivers(new_race):
    r = play(new_race, "demo")
    n = _followed(r)
    before = len([e for e in r.sent if e["title"] == "PIT STOP TIME"])
    r.update("PitStopSeries", {"PitTimes": {n: {"0": {"Timestamp": "x", "PitStop": {
        "RacingNumber": n, "PitStopTime": "2.3", "PitLaneTime": "21.9", "Lap": "12"}}}}})
    r.update("PitLaneTimeCollection", {"PitTimes": {n: {"RacingNumber": n, "Duration": "21.9", "Lap": "12"}}})
    posted = [e for e in r.sent if e["title"] == "PIT STOP TIME"][before:]
    assert len(posted) == 1
    assert "2.3s stationary" in posted[0]["text"] and "21.9s in the pit lane" in posted[0]["text"]
    assert {"num": n, "lap": 12, "stop": 2.3, "lane": 21.9} in r.view()["stops"]


def test_slow_stop_is_called_slow(new_race):
    r = play(new_race, "demo")
    n = _followed(r)
    other = next(m for m in r.lines() if m != n)
    r.update("PitStopSeries", {"PitTimes": {other: [{"PitStop": {"RacingNumber": other, "PitStopTime": "2.4", "Lap": "10"}}]}})
    r.update("PitStopSeries", {"PitTimes": {n: [{"PitStop": {"RacingNumber": n, "PitStopTime": "5.8", "Lap": "11"}}]}})
    assert "A slow one." in [e for e in r.sent if e["title"] == "PIT STOP TIME"][-1]["text"]


def test_pit_lane_time_alone_is_used_when_there_is_no_stop_series(new_race):
    r = play(new_race, "demo")
    n = _followed(r)
    r.update("PitLaneTimeCollection", {"PitTimes": {n: {"RacingNumber": n, "Duration": "23.4", "Lap": "20"}}})
    assert "23.4s in the pit lane" in [e for e in r.sent if e["title"] == "PIT STOP TIME"][-1]["text"]


def test_lap_chart_has_the_grid_and_every_lap(new_race):
    r = play(new_race, "demo")
    chart = r.view()["lapChart"]
    assert chart
    lap, total = r.lap()
    for n, pts in chart.items():
        laps = [p[0] for p in pts]
        assert laps == sorted(laps)
        assert all(1 <= p[1] <= 30 for p in pts)
    assert any(pts[0][0] == 0 for pts in chart.values())       # starts from the grid
    assert max(p[0] for pts in chart.values() for p in pts) >= int(lap) - 1


def test_f1_lap_series_is_preferred_when_sent(new_race):
    r = play(new_race, "demo")
    n = _followed(r)
    r.update("LapSeries", {n: {"RacingNumber": n, "LapPosition": ["7", "3"]}})
    pts = dict((a, b) for a, b in r.lap_chart()[n])
    assert pts[0] == 7 and pts[1] == 3


def test_commentary_mentions_stop_times(new_race):
    import commentary
    r = play(new_race, "demo")
    if not r.pit_log:
        return
    s = r.pit_log[-1]
    r.update("PitStopSeries", {"PitTimes": {s["num"]: [{"PitStop": {
        "RacingNumber": s["num"], "PitStopTime": "4.6", "PitLaneTime": "24.0", "Lap": str(s["lap"])}}]}})
    text = "\n".join(commentary.pit_story(r, commentary._rows(r)))
    assert "4.6s stationary (a slow stop)" in text and "Quickest stop so far" in text
