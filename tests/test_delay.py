"""Holding the live feed back to match the telly."""
import time

import feed


def test_messages_arrive_late_and_in_order():
    got = []
    d = feed.Delayed(lambda topic, data, ts: got.append((topic, time.time())), 0.3)
    t0 = time.time()
    d("A", {}, None)
    d("B", {}, None)
    time.sleep(0.15)
    assert got == []
    time.sleep(0.4)
    assert [g[0] for g in got] == ["A", "B"]
    assert got[0][1] - t0 >= 0.29


def test_no_delay_means_straight_through():
    calls = []
    f = feed.LiveFeed(lambda *a: calls.append(a), lambda s: None)
    assert f.on_data.__class__ is not feed.Delayed
    f2 = feed.LiveFeed(lambda *a: calls.append(a), lambda s: None, delay=20)
    assert isinstance(f2.on_data, feed.Delayed) and f2.delay == 20


def test_session_clock_allows_for_the_delay(new_race):
    r = new_race
    r.delay = 20
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 21))   # stamped 21s ago, shown now
    r.update("ExtrapolatedClock", {"Utc": stamp, "Remaining": "00:10:00", "Extrapolating": True})
    assert 597 <= r.clock_left() <= 600.5     # we're showing the moment 20s ago, so ~1s has gone
    assert r.view()["delay"] == 20
