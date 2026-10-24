"""Builds the playable demo for the project website (docs/demo).

It plays the made-up demo race through the real race logic, takes a snapshot of what the
screen shows every second of playback, and saves those with a small script that stands in
for the dashboard program. The browser then runs the real dashboard page against the
snapshots: no laptop, no F1 connection, no AI key needed. Run it again after changing the
dashboard:  py tools\\build_web_demo.py
"""
import importlib.util
import json
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
spec = importlib.util.spec_from_file_location("config", os.path.join(ROOT, "config.example.py"))
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)
for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_BASE_URL"):
    setattr(config, k, "")
sys.modules["config"] = config

from feed import REPLAY_TOPICS, _parse_stream   # noqa: E402
from race import Race                           # noqa: E402
import track                                    # noqa: E402

SPEED = 15          # playback speed in the browser
OUT = os.path.join(ROOT, "docs", "demo")


class Quiet:
    enabled = False

    def __call__(self, *a, **k):
        pass


def frames(folder="demo"):
    entries = []
    for t in REPLAY_TOPICS:
        f = os.path.join(ROOT, folder, t + ".jsonStream")
        if os.path.exists(f):
            with open(f, encoding="utf-8-sig") as fh:
                entries += _parse_stream(fh.read(), t)
    entries.sort(key=lambda e: e[0])
    race = Race(Quiet())
    out, events, seen = [], [], 0
    step, next_at = SPEED * 1.0, entries[0][0]
    i = 0
    end = entries[-1][0] + 40
    t = entries[0][0]
    while t <= end:
        while i < len(entries) and entries[i][0] <= t:
            race.set_clock(entries[i][0], SPEED)
            race.update(entries[i][1], entries[i][2])
            i += 1
        race.set_clock(t, SPEED)
        v = race.view()
        new = v.pop("events")
        # the feed only ever grows, newest first: keep one copy of each entry
        fresh = len(race.events) - seen
        clock = 20 * 3600 + (t - entries[0][0])          # as if lights out were at 8pm
        for e in reversed(race.events[:fresh]):
            e = dict(e, t="%02d:%02d:%02d" % (clock // 3600 % 24, clock // 60 % 60, clock % 60))
            if e.get("audio"):
                e["radioNote"] = "Not in the demo"
            events.append(e)
        seen = len(race.events)
        v.update(evN=len(events), age=0.5, replay=SPEED, status="Demo race, made up for this page")
        v["ai"] = {"available": False, "on": False}
        v.pop("standings", None)
        out.append(v)
        t += step
    return out, events


def main():
    os.makedirs(OUT, exist_ok=True)
    f, ev = frames()
    with open(os.path.join(OUT, "frames.json"), "w", encoding="utf-8") as fh:
        json.dump({"speed": SPEED, "frames": f, "events": ev}, fh, separators=(",", ":"))
    outline = track.outline(9, 2026)
    with open(os.path.join(OUT, "track.json"), "w", encoding="utf-8") as fh:
        json.dump(outline, fh, separators=(",", ":"))
    page = open(os.path.join(ROOT, "dashboard.html"), encoding="utf-8").read()
    page = page.replace("<head>", '<head>\n<script src="demo-shim.js"></script>', 1)
    page = re.sub(r"<title>.*?</title>", "<title>Ed's Race Weekend Dashboard: demo</title>", page, count=1)
    with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(page)
    shutil.copy(os.path.join(ROOT, "tools", "demo-shim.js"), os.path.join(OUT, "demo-shim.js"))
    static = os.path.join(ROOT, "docs", "static")
    if os.path.exists(static):
        shutil.rmtree(static)
    shutil.copytree(os.path.join(ROOT, "static"), static)
    size = os.path.getsize(os.path.join(OUT, "frames.json"))
    print("Demo built: %d frames, %d feed entries, %.1f MB" % (len(f), len(ev), size / 1e6))


if __name__ == "__main__":
    main()
