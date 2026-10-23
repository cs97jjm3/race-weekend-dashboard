"""Driver and circuit history, for the windows that open when you click a driver or the track.

Everything comes from the free Jolpica results service (the replacement for the old
Ergast service, same layout). Answers are saved in the history_cache folder so each
driver only takes a few seconds the first time, and Jolpica isn't asked twice.
Jolpica asks people to go gently, so requests are spaced out.
"""
import hashlib
import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request

log = logging.getLogger("dashboard")
BASE = os.environ.get("JOLPICA_BASE", "https://api.jolpi.ca/ergast/f1/")
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "history_cache")
HOUR = 3600
_gap_lock = threading.Lock()
_last_call = [0.0]


def _get(path, ttl, **params):
    """One Jolpica call, from the cache if it's fresh enough."""
    params.setdefault("limit", 100)
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    os.makedirs(CACHE, exist_ok=True)
    fn = os.path.join(CACHE, hashlib.sha1(url.encode()).hexdigest()[:20] + ".json")
    try:
        if time.time() - os.path.getmtime(fn) < ttl:
            with open(fn, encoding="utf-8") as f:
                return json.load(f)
    except OSError:
        pass
    with _gap_lock:                      # no more than about three calls a second
        wait = 0.35 - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": "race-weekend-dashboard/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        if os.path.exists(fn):           # out of date is better than nothing
            with open(fn, encoding="utf-8") as f:
                return json.load(f)
        raise
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return data


def _all_races(path, ttl):
    """All pages of a results-style answer, as one list of races."""
    races, offset = [], 0
    while True:
        d = _get(path, ttl, offset=offset)["MRData"]
        page = d["RaceTable"]["Races"]
        # a race can be split across two pages; join its results back up
        for r in page:
            if races and races[-1]["season"] == r["season"] and races[-1]["round"] == r["round"]:
                for k in ("Results", "QualifyingResults", "SprintResults"):
                    races[-1].setdefault(k, []).extend(r.get(k, []))
            else:
                races.append(r)
        offset += int(d["limit"])
        if offset >= int(d["total"]) or not page:
            return races


def _total(path, ttl):
    return int(_get(path, ttl, limit=1)["MRData"]["total"])


def _num(x, default=None):
    try:
        return int(x)
    except (TypeError, ValueError):
        return default


def _finished(res):
    st = res.get("status", "")
    return st in ("Finished", "Lapped") or st.startswith("+")


def _short(res):
    """P1 / DNF / DSQ / DNS for a results row."""
    pt = str(res.get("positionText", res.get("position", "")))
    if pt == "D":
        return "DSQ"
    if pt == "W":
        return "DNS"
    if not pt.isdigit():
        return "DNF"
    return "P" + pt


def driver_id(code):
    d = _get("current/drivers.json", 24 * HOUR)["MRData"]["DriverTable"]["Drivers"]
    for x in d:
        if (x.get("code") or "").upper() == code.upper():
            return x
    return None


def circuit_for(date):
    """The Jolpica circuit for the race weekend that a session on this date belongs to."""
    races = _get("current/races.json", 24 * HOUR)["MRData"]["RaceTable"]["Races"]
    day = str(date)[:10]
    best = None
    for r in races:
        if not r.get("date"):
            continue
        gap = (time.mktime(time.strptime(r["date"], "%Y-%m-%d")) - time.mktime(time.strptime(day, "%Y-%m-%d"))) / 86400
        if -1 <= gap <= 4 and (best is None or gap < best[0]):
            best = (gap, r)
    return best[1] if best else None


def driver(code, session_date=None):
    who = driver_id(code)
    if not who:
        return {"error": "Couldn't find %s in this season's drivers" % code}
    did = who["driverId"]
    p = "drivers/%s/" % did
    career = {
        "races": _total(p + "results.json", 12 * HOUR),
        "wins": _total(p + "results/1.json", 12 * HOUR),
        "podiums": sum(_total(p + "results/%d.json" % n, 12 * HOUR) for n in (1, 2, 3)),
        "poles": _total(p + "qualifying/1.json", 12 * HOUR),
    }
    # Jolpica only gives standings season by season, so count titles one past season at a
    # time. Finished seasons never change, so those are kept for a year.
    try:
        seasons = [x["season"] for x in _get(p + "seasons.json", 24 * HOUR)["MRData"]["SeasonTable"]["Seasons"]]
        career["firstYear"] = seasons[0] if seasons else ""
        titles = 0
        for y in seasons:
            if int(y) >= int(time.strftime("%Y")):
                continue
            lists = _get("%s/drivers/%s/driverStandings.json" % (y, did), 365 * 24 * HOUR)["MRData"]["StandingsTable"]["StandingsLists"]
            if lists and str(lists[0]["DriverStandings"][0].get("position")) == "1":
                titles += 1
        career["titles"] = titles
    except Exception as e:
        log.info("Couldn't count titles for %s (%s)", did, e)
        career["titles"] = "?"

    # this season, race by race
    season, team = [], None
    for r in _all_races("current/drivers/%s/results.json" % did, HOUR):
        res = (r.get("Results") or [{}])[0]
        team = res.get("Constructor") or team
        season.append({"round": _num(r["round"]), "race": r["raceName"].replace(" Grand Prix", ""),
                       "pos": _num(res.get("position")), "label": _short(res), "grid": _num(res.get("grid")),
                       "points": float(res.get("points") or 0), "finished": _finished(res)})

    # at this circuit, every year
    here, circuit = [], None
    if session_date:
        try:
            rw = circuit_for(session_date)
            circuit = rw and rw["Circuit"]
        except Exception as e:
            log.info("Couldn't work out the circuit (%s)", e)
    if circuit:
        for r in _all_races("circuits/%s/drivers/%s/results.json" % (circuit["circuitId"], did), 12 * HOUR):
            res = (r.get("Results") or [{}])[0]
            here.append({"year": r["season"], "label": _short(res), "pos": _num(res.get("position")),
                         "grid": _num(res.get("grid")), "team": (res.get("Constructor") or {}).get("name", "")})
        here.reverse()

    # against the teammate, this season
    mate = None
    if team:
        tid = team["constructorId"]
        ahead = {"race": [0, 0], "quali": [0, 0], "points": [0.0, 0.0]}
        mate_name = None
        for r in _all_races("current/constructors/%s/results.json" % tid, HOUR):
            rows = {x["Driver"]["driverId"]: x for x in r.get("Results", [])}
            if did not in rows or len(rows) < 2:
                continue
            other = next(k for k in rows if k != did)
            mate_name = rows[other]["Driver"]
            a, b = rows[did], rows[other]
            ahead["points"][0] += float(a.get("points") or 0)
            ahead["points"][1] += float(b.get("points") or 0)
            pa, pb = _num(a.get("position"), 99), _num(b.get("position"), 99)
            ahead["race"][0 if pa < pb else 1] += 1
        for r in _all_races("current/constructors/%s/qualifying.json" % tid, HOUR):
            rows = {x["Driver"]["driverId"]: _num(x.get("position"), 99) for x in r.get("QualifyingResults", [])}
            if did in rows and len(rows) >= 2:
                other = min((v for k, v in rows.items() if k != did), default=99)
                ahead["quali"][0 if rows[did] < other else 1] += 1
        if mate_name:
            mate = {"name": "%s %s" % (mate_name.get("givenName", ""), mate_name.get("familyName", "")),
                    "code": mate_name.get("code", ""), **ahead}

    return {
        "code": code.upper(), "name": "%s %s" % (who.get("givenName", ""), who.get("familyName", "")),
        "number": who.get("permanentNumber", ""), "nationality": who.get("nationality", ""),
        "born": who.get("dateOfBirth", ""), "team": (team or {}).get("name", ""),
        "career": career, "season": season, "here": here,
        "circuit": circuit and circuit.get("circuitName"), "mate": mate,
    }


def circuit(session_date):
    rw = circuit_for(session_date)
    if not rw:
        return {"error": "Couldn't match this session to a race on the calendar"}
    c = rw["Circuit"]
    cid = c["circuitId"]
    winners = []
    for r in _all_races("circuits/%s/results/1.json" % cid, 12 * HOUR):
        res = (r.get("Results") or [{}])[0]
        d = res.get("Driver") or {}
        winners.append({"year": r["season"], "race": r["raceName"], "driver": d.get("familyName", ""),
                        "code": d.get("code", ""), "team": (res.get("Constructor") or {}).get("name", ""),
                        "grid": _num(res.get("grid"))})
    poles = {}
    try:
        for r in _all_races("circuits/%s/qualifying/1.json" % cid, 12 * HOUR):
            q = (r.get("QualifyingResults") or [{}])[0]
            poles[r["season"]] = (q.get("Driver") or {}).get("familyName", "")
    except Exception as e:
        log.info("No pole history (%s)", e)
    for w in winners:
        w["pole"] = poles.get(w["year"], "")
    count, teams = {}, {}
    for w in winners:
        count[w["driver"]] = count.get(w["driver"], 0) + 1
        teams[w["team"]] = teams.get(w["team"], 0) + 1
    top = sorted(count.items(), key=lambda x: -x[1])[:5]
    top_teams = sorted(teams.items(), key=lambda x: -x[1])[:5]
    from_pole = sum(1 for w in winners if w["grid"] == 1)
    winners.reverse()
    return {"name": c.get("circuitName", ""), "place": "%s, %s" % (c["Location"].get("locality", ""), c["Location"].get("country", "")),
            "race": rw.get("raceName", ""), "first": winners[-1]["year"] if winners else "", "count": len(winners),
            "winners": winners, "mostWins": top, "mostTeams": top_teams, "fromPole": from_pole,
            "deepest": max(winners, key=lambda w: 99 if w["grid"] == 0 else (w["grid"] or 0)) if winners else None}
