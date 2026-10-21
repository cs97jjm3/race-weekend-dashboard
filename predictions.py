"""The predictions game: James and Ed each pick a podium before the race.

Scoring: 5 points for a driver in exactly the right place, 2 for a driver who
makes the podium but in a different place. So a perfect podium is worth 15.

Picks lock at lights out. Results and season totals are kept in
predictions.json next to the dashboard. Replays and the demo can be played,
but they're never saved, so they don't count towards the season.
"""
import json
import os
import threading
import time

import config

HERE = os.path.dirname(os.path.abspath(__file__))
FILE = os.path.join(HERE, "predictions.json")
EXACT, PODIUM = 5, 2


def score(picks, result):
    pts = 0
    for i, code in enumerate(picks[:3]):
        if not code:
            continue
        if i < len(result) and result[i] == code:
            pts += EXACT
        elif code in result[:3]:
            pts += PODIUM
    return pts


class Predictions:
    def __init__(self, race):
        self.race = race
        self.lock = threading.Lock()
        self.data = {"events": {}}
        self.scratch = {}         # replays and the demo live here, never saved
        try:
            with open(FILE, encoding="utf-8") as f:
                self.data = json.load(f)
        except FileNotFoundError:
            pass
        except Exception:
            # a damaged file shouldn't stop the dashboard; keep a copy for later
            os.replace(FILE, FILE + ".broken")
        self.data.setdefault("events", {})
        race.finish_hooks.append(self.finish)

    # ----- helpers -----

    def _live(self):
        return self.race.replay_speed is None

    def _key(self):
        s = self.race.session()
        meeting = (s.get("Meeting") or {}).get("Name", "")
        if not meeting or not self.race.is_race():
            return None
        return "%s|%s|%s" % (str(s.get("StartDate", ""))[:4] or time.strftime("%Y"), meeting, s.get("Name", ""))

    def _store(self):
        return self.data["events"] if self._live() else self.scratch

    def _save(self):
        if not self._live():
            return
        tmp = FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=1)
        os.replace(tmp, FILE)

    def _locked(self):
        return self.race.session_status in ("Started", "Finished", "Finalised", "Aborted") and self._live()

    # ----- for the screen -----

    def state(self):
        with self.lock:
            key = self._key()
            ev = self._store().get(key) if key else None
            season = {}
            history = []
            for k, e in sorted(self.data["events"].items()):
                if e.get("scores"):
                    for person, pts in e["scores"].items():
                        season[person] = season.get(person, 0) + pts
                    history.append({"name": e.get("name", k), "scores": e["scores"], "result": e.get("result", [])})
            return {
                "key": key, "name": ev.get("name") if ev else None,
                "open": bool(key) and not self._locked() and not (ev and ev.get("scores")),
                "picks": (ev or {}).get("picks", {}), "result": (ev or {}).get("result"),
                "scores": (ev or {}).get("scores"), "season": season, "history": history[-12:],
                "people": list(config.PEOPLE), "counts": self._live(),
                "why": None if key else "Picks open when the dashboard is connected to a race or sprint",
            }

    def pick(self, person, picks):
        with self.lock:
            key = self._key()
            if not key:
                return {"error": "Not a race or sprint session"}
            if person not in config.PEOPLE:
                return {"error": "Who's %s?" % person}
            if self._locked():
                return {"error": "Too late, the race has started"}
            picks = [str(p or "").upper()[:3] for p in (picks or [])][:3]
            if len([p for p in picks if p]) != len(set(p for p in picks if p)):
                return {"error": "Pick three different drivers"}
            s = self.race.session()
            ev = self._store().setdefault(key, {"name": "%s %s" % ((s.get("Meeting") or {}).get("Name", ""),
                                                                    s.get("Name", "")), "picks": {}})
            if ev.get("scores"):
                return {"error": "This one's already been scored"}
            ev["picks"][person] = picks
            self._save()
            return {"ok": True}

    # ----- at the chequered flag -----

    def finish(self, order):
        with self.lock:
            key = self._key()
            ev = self._store().get(key) if key else None
            if not ev or not ev.get("picks") or ev.get("scores"):
                return
            result = [self.race.tla(n) for n in order[:3]]
            ev["result"] = result
            ev["scores"] = {p: score(picks, result) for p, picks in ev["picks"].items()}
            self._save()
            totals = {}
            if self._live():
                for e in self.data["events"].values():
                    for p, pts in (e.get("scores") or {}).items():
                        totals[p] = totals.get(p, 0) + pts
            text = "Predictions: " + ", ".join("%s %d points" % (p, s) for p, s in ev["scores"].items()) + "."
            if totals:
                text += " Season so far: " + ", ".join("%s %d" % (p, s) for p, s in sorted(totals.items(), key=lambda x: -x[1])) + "."
            else:
                text += " (Replay, so not added to the season.)"
            self.race.emit(text, title="PREDICTIONS", kind="lead", send=True)
