"""Shared set-up for the tests.

The tests always use config.example.py (James and Ed's example settings), never
anyone's own config.py, so they give the same answer on every computer.
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location("config", os.path.join(ROOT, "config.example.py"))
config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(config)
config.ANTHROPIC_API_KEY = ""          # never call Claude from a test
sys.modules["config"] = config

import pytest  # noqa: E402


class Recorder:
    """Stands in for the notifier: keeps every event the race sends out."""
    enabled = False

    def __init__(self):
        self.events = []

    def __call__(self, event, loud=False):
        self.events.append(dict(event, loud=loud))


def play(race, folder):
    """Feed a recorded session into a Race, in order, as fast as possible."""
    from feed import REPLAY_TOPICS, _parse_stream
    entries = []
    for topic in REPLAY_TOPICS:
        f = os.path.join(ROOT, folder, topic + ".jsonStream")
        if os.path.exists(f):
            with open(f, encoding="utf-8-sig") as fh:
                entries += _parse_stream(fh.read(), topic)
    entries.sort(key=lambda e: e[0])
    for t, topic, data in entries:
        race.set_clock(t, 1.0)
        race.update(topic, data)
    race.set_clock(entries[-1][0] + 60, 1.0)
    race.update("Heartbeat", {})
    return race


@pytest.fixture
def new_race():
    from race import Race
    rec = Recorder()
    r = Race(rec)
    r.sent = rec.events
    return r


@pytest.fixture(autouse=True)
def no_rain_forecast_lookups(monkeypatch):
    """The commentary asks Open-Meteo for a rain forecast; tests never go online."""
    import commentary
    monkeypatch.setattr(commentary, "forecast", lambda race: None)
