"""Championship standings, 'if it finished now'.

Starts from the current standings on the free Jolpica results service (the
replacement for the old Ergast service) and adds the points each driver would
get for where they are right now. Fetched in the background and refreshed
every half hour, so the screen never waits on it.
"""
import json
import logging
import threading
import time
import urllib.request

import config

log = logging.getLogger("dashboard")
BASE = "https://api.jolpi.ca/ergast/f1/current/"
RACE_POINTS = [25, 18, 15, 12, 10, 8, 6, 4, 2, 1]
SPRINT_POINTS = [8, 7, 6, 5, 4, 3, 2, 1]


def _get(path):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "race-weekend-dashboard/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


class Standings:
    def __init__(self):
        self.data = None          # {"round", "drivers": [...], "teams": [...], "schedule": {...}}
        self.error = None
        self.fetched = 0
        self.busy = False

    def _fetch(self):
        try:
            d = _get("driverStandings.json")
            lists = d["MRData"]["StandingsTable"]["StandingsLists"]
            drivers, rnd = [], 0
            if lists:
                rnd = int(lists[0].get("round") or 0)
                for s in lists[0]["DriverStandings"]:
                    dr = s["Driver"]
                    cons = (s.get("Constructors") or [{}])[-1]
                    drivers.append({"code": dr.get("code") or dr.get("familyName", "")[:3].upper(),
                                    "name": dr.get("familyName", ""), "points": float(s.get("points") or 0),
                                    "team": cons.get("name", ""), "teamId": cons.get("constructorId", "")})
            c = _get("constructorStandings.json")
            clists = c["MRData"]["StandingsTable"]["StandingsLists"]
            teams = []
            if clists:
                for s in clists[0]["ConstructorStandings"]:
                    k = s["Constructor"]
                    teams.append({"id": k.get("constructorId", ""), "name": k.get("name", ""),
                                  "points": float(s.get("points") or 0)})
            sched = _get("races.json")
            races = sched["MRData"]["RaceTable"]["Races"]
            self.data = {"round": rnd, "drivers": drivers, "teams": teams,
                         "races": [{"round": int(r["round"]), "date": r.get("date", ""),
                                    "sprint": (r.get("Sprint") or {}).get("date", "")} for r in races]}
            self.error = None
            log.info("Championship standings loaded (after round %d)", rnd)
        except Exception as e:
            self.error = "Couldn't get the standings (%s)" % e
            log.info(self.error)
        finally:
            self.fetched = time.time()
            self.busy = False

    def refresh(self):
        if self.busy:
            return
        if self.data and time.time() - self.fetched < 1800:
            return
        if not self.data and self.error and time.time() - self.fetched < 120:
            return
        self.busy = True
        threading.Thread(target=self._fetch, daemon=True).start()

    def _this_round(self, race):
        """Which round of the championship this session belongs to, from its date."""
        day = str(race.session().get("StartDate", ""))[:10]
        for r in (self.data or {}).get("races", []):
            if day and day in (r["date"], r["sprint"]):
                return r["round"]
        return None

    def live(self, race, rows):
        self.refresh()
        if not self.data:
            return {"error": self.error or "Loading the standings..."}
        drivers = [dict(d) for d in self.data["drivers"]]
        teams = {t["id"]: dict(t) for t in self.data["teams"]}
        rnd = self._this_round(race)
        scoring = race.is_race() and race.session_status in ("Started", "Finished", "Finalised", "Aborted")
        note = "Standings before this session"
        if rnd and self.data["round"] >= rnd:
            scoring = False
            note = "These standings already include this race"
        elif scoring:
            note = "If it finished now"
        elif not race.is_race():
            note = "Current standings"
        table = RACE_POINTS if not race.is_sprint() else SPRINT_POINTS
        order = [r for r in rows if not r["out"] and r["pos"] < 99]
        by_code = {d["code"]: d for d in drivers}
        for i, r in enumerate(order):
            d = by_code.get(r["tla"])
            if d is None:
                d = {"code": r["tla"], "name": r["name"], "points": 0.0, "team": r["team"], "teamId": r["team"].lower()}
                drivers.append(d)
                by_code[r["tla"]] = d
            d["live"] = r["pos"]
            d["gain"] = table[i] if scoring and i < len(table) else 0
        watch = {r["tla"]: r["watchers"] for r in rows}
        row_team = {r["tla"]: r["team"] for r in rows}
        for d in drivers:
            d.setdefault("gain", 0)
            d["before"] = d["points"]
            d["after"] = d["points"] + d["gain"]
            d["watchers"] = watch.get(d["code"], [])
            t = teams.setdefault(d["teamId"], {"id": d["teamId"], "name": d["team"], "points": 0.0})
            t["gain"] = t.get("gain", 0) + d["gain"]
            names = " ".join([t["name"], row_team.get(d["code"], "")]).lower()
            fans = t.setdefault("watchers", set())
            for person, prefs in config.PEOPLE.items():
                if any(x.lower() in names for x in prefs.get("teams", []) if x):
                    fans.add(person)
        old_rank = {d["code"]: i for i, d in enumerate(sorted(drivers, key=lambda d: -d["before"]))}
        drivers.sort(key=lambda d: (-d["after"], old_rank.get(d["code"], 99)))
        for i, d in enumerate(drivers):
            d["move"] = old_rank.get(d["code"], i) - i
        tlist = list(teams.values())
        for t in tlist:
            t.setdefault("gain", 0)
            t["after"] = t["points"] + t["gain"]
            t["watchers"] = sorted(t.get("watchers") or [])
        old_t = {t["id"]: i for i, t in enumerate(sorted(tlist, key=lambda t: -t["points"]))}
        tlist.sort(key=lambda t: (-t["after"], old_t.get(t["id"], 99)))
        for i, t in enumerate(tlist):
            t["move"] = old_t.get(t["id"], i) - i
        title = None
        try:
            title = self.title_fight(race, drivers, scoring)
        except Exception as e:
            log.info("Title fight sums skipped (%s)", e)
        return {"note": note, "round": self.data["round"], "drivers": drivers, "teams": tlist, "title": title}

    def points_left(self, race, scoring):
        """Points still to be won after this session: (points, races left, sprints left)."""
        cur = self._this_round(race) or (self.data["round"] + 1)
        races = sprints = 0
        for r in self.data["races"]:
            if r["round"] > cur:
                races += 1
                sprints += 1 if r["sprint"] else 0
            elif r["round"] == cur:
                name = (race.session().get("Name") or "").lower()
                if race.is_sprint():
                    races += 1                     # the grand prix is still to come this weekend
                elif race.is_race():
                    pass                           # this is the last points of the weekend
                else:
                    races += 1
                    if r["sprint"] and ("practice" in name or "sprint" in name):
                        sprints += 1               # sprint qualifying or practice: the sprint is still to come
        return races * RACE_POINTS[0] + sprints * SPRINT_POINTS[0], races, sprints

    def title_fight(self, race, drivers, scoring):
        """Who can still win the title: before this session, and if it finished as things are now."""
        if not drivers:
            return None
        left, races, sprints = self.points_left(race, scoring)
        this_session = (SPRINT_POINTS[0] if race.is_sprint() else RACE_POINTS[0]) if scoring else 0
        lead_after = max(d["after"] for d in drivers)
        lead_before = max(d["before"] for d in drivers)
        out = []
        for d in sorted(drivers, key=lambda d: -d["after"]):
            alive_now = d["after"] + left >= lead_after
            alive_before = d["before"] + left + this_session >= lead_before
            if alive_now or alive_before:
                out.append({"code": d["code"], "name": d["name"], "after": d["after"], "before": d["before"],
                            "behind": lead_after - d["after"], "alive": alive_now, "was": alive_before,
                            "watchers": d.get("watchers", [])})
        alive = [c for c in out if c["alive"]]
        champion = alive[0]["code"] if len(alive) == 1 and (scoring or left == 0) else None
        return {"left": left, "races": races, "sprints": sprints, "contenders": out[:6],
                "champion": champion, "live": scoring}
