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


_resting = [0.0]


class TooBusy(Exception):
    def __str__(self):
        mins = max(1, int((_resting[0] - time.time()) // 60) + 1)
        return ("Jolpica (the free results service) has had enough lookups from us for now. "
                "Anything already looked up still works; try new ones again in about %d minutes." % mins)


def _stale(fn):
    try:
        with open(fn, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


NEW_RESULTS = "new-results"     # a ttl meaning "keep until a new race or qualifying result appears"
_pace = threading.local()        # the background warm-up goes slower than clicks


def _results_tag():
    """Changes whenever Jolpica gets a new race or qualifying result this season.
    Checked at most once an hour, two tiny lookups."""
    return "%s-%s" % (_total("current/results.json", HOUR), _total("current/qualifying.json", HOUR))


def _get(path, ttl, **params):
    """One Jolpica call, from the cache if it's fresh enough."""
    params.setdefault("limit", 100)
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    key = url
    if ttl == NEW_RESULTS:
        # career numbers and this season's results only change after a session,
        # so keep them until Jolpica has something new, rather than on a timer
        key, ttl = url + "#" + _results_tag(), 30 * 24 * HOUR
    os.makedirs(CACHE, exist_ok=True)
    fn = os.path.join(CACHE, hashlib.sha1(key.encode()).hexdigest()[:20] + ".json")
    try:
        if time.time() - os.path.getmtime(fn) < ttl:
            with open(fn, encoding="utf-8") as f:
                return json.load(f)
    except OSError:
        pass
    if time.time() < _resting[0]:
        stale = _stale(fn)
        if stale is not None:
            return stale
        raise TooBusy()
    with _gap_lock:                      # no more than about two calls a second
        wait = getattr(_pace, "gap", 0.6) - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": "race-weekend-dashboard/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        if getattr(e, "code", None) == 429:
            # Jolpica allows a few hundred lookups an hour; if we've hit that, leave it alone for a bit
            try:
                rest = int(e.headers.get("Retry-After") or 0)
            except (TypeError, ValueError, AttributeError):
                rest = 0
            _resting[0] = time.time() + max(rest, 600)
            log.info("Jolpica says we've asked too much; resting it for %d minutes", max(rest, 600) // 60)
        stale = _stale(fn)               # out of date is better than nothing
        if stale is not None:
            return stale
        if getattr(e, "code", None) == 429:
            raise TooBusy()
        raise
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return data

# World champions by year (Jolpica driver ids). Past titles never change, so they're kept
# here rather than asking Jolpica one season at a time. Later years are looked up if missing.
CHAMPIONS = {
    1950: "farina", 1951: "fangio", 1952: "ascari", 1953: "ascari", 1954: "fangio", 1955: "fangio",
    1956: "fangio", 1957: "fangio", 1958: "hawthorn", 1959: "brabham", 1960: "brabham", 1961: "phil_hill",
    1962: "hill", 1963: "clark", 1964: "surtees", 1965: "clark", 1966: "brabham", 1967: "hulme", 1968: "hill",
    1969: "stewart", 1970: "rindt", 1971: "stewart", 1972: "emerson_fittipaldi", 1973: "stewart",
    1974: "emerson_fittipaldi", 1975: "lauda", 1976: "hunt", 1977: "lauda", 1978: "mario_andretti",
    1979: "scheckter", 1980: "jones", 1981: "piquet", 1982: "keke_rosberg", 1983: "piquet", 1984: "lauda",
    1985: "prost", 1986: "prost", 1987: "piquet", 1988: "senna", 1989: "prost", 1990: "senna", 1991: "senna",
    1992: "mansell", 1993: "prost", 1994: "michael_schumacher", 1995: "michael_schumacher", 1996: "damon_hill",
    1997: "villeneuve", 1998: "hakkinen", 1999: "hakkinen", 2000: "michael_schumacher",
    2001: "michael_schumacher", 2002: "michael_schumacher", 2003: "michael_schumacher",
    2004: "michael_schumacher", 2005: "alonso", 2006: "alonso", 2007: "raikkonen", 2008: "hamilton",
    2009: "button", 2010: "vettel", 2011: "vettel", 2012: "vettel", 2013: "vettel", 2014: "hamilton",
    2015: "hamilton", 2016: "rosberg", 2017: "hamilton", 2018: "hamilton", 2019: "hamilton", 2020: "hamilton",
    2021: "max_verstappen", 2022: "max_verstappen", 2023: "max_verstappen", 2024: "max_verstappen",
    2025: "norris",
}


def _champion(year):
    if year in CHAMPIONS:
        return CHAMPIONS[year]
    lists = _get("%d/driverStandings/1.json" % year, 365 * 24 * HOUR)["MRData"]["StandingsTable"]["StandingsLists"]
    return lists[0]["DriverStandings"][0]["Driver"]["driverId"] if lists else None

# Constructors' champions by year (Jolpica team ids), for the same reason.
TEAM_CHAMPIONS = {
    1958: "vanwall", 1959: "cooper", 1960: "cooper", 1961: "ferrari", 1962: "brm", 1963: "team_lotus",
    1964: "ferrari", 1965: "team_lotus", 1966: "brabham", 1967: "brabham", 1968: "team_lotus", 1969: "matra",
    1970: "team_lotus", 1971: "tyrrell", 1972: "team_lotus", 1973: "team_lotus", 1974: "mclaren",
    1975: "ferrari", 1976: "ferrari", 1977: "ferrari", 1978: "team_lotus", 1979: "ferrari", 1980: "williams",
    1981: "williams", 1982: "ferrari", 1983: "ferrari", 1984: "mclaren", 1985: "mclaren", 1986: "williams",
    1987: "williams", 1988: "mclaren", 1989: "mclaren", 1990: "mclaren", 1991: "mclaren", 1992: "williams",
    1993: "williams", 1994: "williams", 1995: "benetton", 1996: "williams", 1997: "williams", 1998: "mclaren",
    1999: "ferrari", 2000: "ferrari", 2001: "ferrari", 2002: "ferrari", 2003: "ferrari", 2004: "ferrari",
    2005: "renault", 2006: "renault", 2007: "ferrari", 2008: "ferrari", 2009: "brawn", 2010: "red_bull",
    2011: "red_bull", 2012: "red_bull", 2013: "red_bull", 2014: "mercedes", 2015: "mercedes", 2016: "mercedes",
    2017: "mercedes", 2018: "mercedes", 2019: "mercedes", 2020: "mercedes", 2021: "mercedes", 2022: "red_bull",
    2023: "red_bull", 2024: "mclaren", 2025: "mclaren",
}


def _team_champion(year):
    if year in TEAM_CHAMPIONS:
        return TEAM_CHAMPIONS[year]
    lists = _get("%d/constructorStandings/1.json" % year, 365 * 24 * HOUR)["MRData"]["StandingsTable"]["StandingsLists"]
    return lists[0]["ConstructorStandings"][0]["Constructor"]["constructorId"] if lists else None


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
        "races": _total(p + "results.json", NEW_RESULTS),
        "wins": _total(p + "results/1.json", NEW_RESULTS),
        "podiums": sum(_total(p + "results/%d.json" % n, NEW_RESULTS) for n in (1, 2, 3)),
        "poles": _total(p + "qualifying/1.json", NEW_RESULTS),
    }
    try:
        seasons = [x["season"] for x in _get(p + "seasons.json", 24 * HOUR)["MRData"]["SeasonTable"]["Seasons"]]
        career["firstYear"] = seasons[0] if seasons else ""
        this_year = int(time.strftime("%Y"))
        career["titles"] = sum(1 for y in seasons if int(y) < this_year and _champion(int(y)) == did)
    except Exception as e:
        log.info("Couldn't count titles for %s (%s)", did, e)
        career["titles"] = "?"

    # this season, race by race
    season, team = [], None
    for r in _all_races("current/drivers/%s/results.json" % did, NEW_RESULTS):
        res = (r.get("Results") or [{}])[0]
        team = res.get("Constructor") or team
        season.append({"round": _num(r["round"]), "year": r["season"], "race": r["raceName"].replace(" Grand Prix", ""),
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
        for r in _all_races("circuits/%s/drivers/%s/results.json" % (circuit["circuitId"], did), NEW_RESULTS):
            res = (r.get("Results") or [{}])[0]
            here.append({"year": r["season"], "round": _num(r["round"]), "label": _short(res), "pos": _num(res.get("position")),
                         "grid": _num(res.get("grid")), "team": (res.get("Constructor") or {}).get("name", "")})
        here.reverse()

    # against the teammate, this season
    mate = None
    if team:
        tid = team["constructorId"]
        ahead = {"race": [0, 0], "quali": [0, 0], "points": [0.0, 0.0]}
        mate_name = None
        for r in _all_races("current/constructors/%s/results.json" % tid, NEW_RESULTS):
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
        for r in _all_races("current/constructors/%s/qualifying.json" % tid, NEW_RESULTS):
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
    for r in _all_races("circuits/%s/results/1.json" % cid, NEW_RESULTS):
        res = (r.get("Results") or [{}])[0]
        d = res.get("Driver") or {}
        winners.append({"year": r["season"], "round": _num(r["round"]), "race": r["raceName"], "driver": d.get("familyName", ""),
                        "code": d.get("code", ""), "team": (res.get("Constructor") or {}).get("name", ""),
                        "grid": _num(res.get("grid"))})
    poles = {}
    try:
        for r in _all_races("circuits/%s/qualifying/1.json" % cid, NEW_RESULTS):
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


def race(season, rnd):
    """Everything about one Grand Prix: the result, qualifying, and the headlines."""
    season, rnd = int(season), int(rnd)
    old = season < int(time.strftime("%Y"))
    ttl = 365 * 24 * HOUR if old else 6 * HOUR
    races = _all_races("%d/%d/results.json" % (season, rnd), ttl)
    if not races:
        return {"error": "No result for that race yet"}
    r = races[0]
    rows = []
    fastest = None
    for x in r.get("Results", []):
        d, k = x.get("Driver") or {}, x.get("Constructor") or {}
        fl = x.get("FastestLap") or {}
        row = {"pos": _num(x.get("position")), "label": _short(x), "code": d.get("code", ""),
               "name": "%s %s" % (d.get("givenName", ""), d.get("familyName", "")), "team": k.get("name", ""),
               "grid": _num(x.get("grid")), "laps": _num(x.get("laps")), "points": float(x.get("points") or 0),
               "time": (x.get("Time") or {}).get("time") or x.get("status", ""),
               "fastest": (fl.get("Time") or {}).get("time", ""), "fastestRank": _num(fl.get("rank"))}
        if row["grid"] is not None and row["pos"] is not None and _finished(x):
            row["gained"] = (row["grid"] or len(r["Results"])) - row["pos"]
        rows.append(row)
        if row["fastestRank"] == 1:
            fastest = row
    quali = []
    try:
        q = _all_races("%d/%d/qualifying.json" % (season, rnd), ttl)
        for x in (q[0].get("QualifyingResults", []) if q else []):
            d = x.get("Driver") or {}
            quali.append({"pos": _num(x.get("position")), "code": d.get("code", ""), "name": d.get("familyName", ""),
                          "team": (x.get("Constructor") or {}).get("name", ""),
                          "q1": x.get("Q1", ""), "q2": x.get("Q2", ""), "q3": x.get("Q3", "")})
    except Exception as e:
        log.info("No qualifying for %s/%s (%s)", season, rnd, e)
    movers = [x for x in rows if x.get("gained") is not None]
    gainer = max(movers, key=lambda x: x["gained"]) if movers else None
    out = [x for x in rows if x["label"] in ("DNF", "DSQ", "DNS")]
    c = r.get("Circuit") or {}
    return {"season": season, "round": rnd, "name": r.get("raceName", ""), "date": r.get("date", ""),
            "circuit": c.get("circuitName", ""), "place": "%s, %s" % ((c.get("Location") or {}).get("locality", ""),
                                                                      (c.get("Location") or {}).get("country", "")),
            "results": rows, "quali": quali, "fastest": fastest,
            "gainer": gainer if gainer and gainer["gained"] > 0 else None,
            "retired": len(out), "laps": rows[0]["laps"] if rows else None}


def team(code, session_date=None):
    """The team a driver (by their three-letter code) drives for this season."""
    who = driver_id(code)
    if not who:
        return {"error": "Couldn't find %s in this season's drivers" % code}
    cons = _get("current/drivers/%s/constructors.json" % who["driverId"], 24 * HOUR)["MRData"]["ConstructorTable"]["Constructors"]
    if not cons:
        return {"error": "Couldn't work out %s's team" % code}
    t = cons[-1]
    tid = t["constructorId"]
    p = "constructors/%s/" % tid
    this_year = int(time.strftime("%Y"))
    career = {
        "wins": _total(p + "results/1.json", NEW_RESULTS),
        "podiums": sum(_total(p + "results/%d.json" % n, NEW_RESULTS) for n in (1, 2, 3)),
        "poles": _total(p + "qualifying/1.json", NEW_RESULTS),
    }
    try:
        seasons = [x["season"] for x in _get(p + "seasons.json", 24 * HOUR)["MRData"]["SeasonTable"]["Seasons"]]
        career["seasons"] = len(seasons)
        career["firstYear"] = seasons[0] if seasons else ""
        career["titles"] = sum(1 for y in seasons if int(y) < this_year and _team_champion(int(y)) == tid)
    except Exception as e:
        log.info("Couldn't count titles for %s (%s)", tid, e)
        career["titles"] = "?"

    # this season, both cars race by race
    season, drivers = [], {}
    for r in _all_races("current/constructors/%s/results.json" % tid, NEW_RESULTS):
        cars = []
        for x in r.get("Results", []):
            d = x.get("Driver") or {}
            code_ = d.get("code", "")
            drivers.setdefault(code_, {"code": code_, "name": "%s %s" % (d.get("givenName", ""), d.get("familyName", "")),
                                       "points": 0.0, "best": None})
            pts = float(x.get("points") or 0)
            drivers[code_]["points"] += pts
            pos = _num(x.get("position"))
            if _short(x).startswith("P") and (drivers[code_]["best"] is None or pos < drivers[code_]["best"]):
                drivers[code_]["best"] = pos
            cars.append({"code": code_, "label": _short(x), "pos": pos, "points": pts})
        cars.sort(key=lambda c: c["pos"] or 99)
        season.append({"round": _num(r["round"]), "year": r["season"], "race": r["raceName"].replace(" Grand Prix", ""),
                       "cars": cars, "points": sum(c["points"] for c in cars)})

    # where they stand this year
    standing = None
    try:
        lists = _get("current/constructorStandings.json", NEW_RESULTS)["MRData"]["StandingsTable"]["StandingsLists"]
        for x in (lists[0]["ConstructorStandings"] if lists else []):
            if x["Constructor"]["constructorId"] == tid:
                standing = {"pos": _num(x.get("position")), "points": float(x.get("points") or 0), "wins": _num(x.get("wins"))}
    except Exception as e:
        log.info("No constructor standings (%s)", e)

    # best finish at this circuit each year
    here, circuit = [], None
    if session_date:
        try:
            rw = circuit_for(session_date)
            circuit = rw and rw["Circuit"]
        except Exception as e:
            log.info("Couldn't work out the circuit (%s)", e)
    if circuit:
        for r in _all_races("circuits/%s/constructors/%s/results.json" % (circuit["circuitId"], tid), NEW_RESULTS):
            res = sorted(r.get("Results", []), key=lambda x: _num(x.get("position"), 99))
            if not res:
                continue
            best = res[0]
            here.append({"year": r["season"], "round": _num(r["round"]), "label": _short(best), "pos": _num(best.get("position")),
                         "who": (best.get("Driver") or {}).get("familyName", "")})
        here.reverse()

    import config
    fans = [n for n, pr in config.PEOPLE.items()
            if any(x and x.lower() in t.get("name", "").lower() for x in pr.get("teams", []))]
    return {"id": tid, "name": t.get("name", ""), "nationality": t.get("nationality", ""), "fans": fans,
            "career": career, "season": season, "drivers": sorted(drivers.values(), key=lambda d: -d["points"]),
            "standing": standing, "here": here, "circuit": circuit and circuit.get("circuitName")}


def warm_up(codes, session_date, gap=2.0):
    """Quietly look up the family's drivers, their teams and the track in the background,
    one lookup every couple of seconds, so the first click opens straight away."""
    _pace.gap = gap
    done_teams = set()
    n0 = len(os.listdir(CACHE)) if os.path.isdir(CACHE) else 0
    for code in codes:
        try:
            d = driver(code, session_date)
            if d.get("team") and d["team"] not in done_teams:
                done_teams.add(d["team"])
                team(code, session_date)
        except TooBusy:
            log.info("History warm-up paused: Jolpica is busy")
            return
        except Exception as e:
            log.info("History warm-up skipped %s (%s)", code, e)
    try:
        circuit(session_date)
    except Exception as e:
        log.info("History warm-up skipped the track (%s)", e)
    n1 = len(os.listdir(CACHE)) if os.path.isdir(CACHE) else 0
    log.info("Driver and team history ready for %s (%d new lookups)", ", ".join(codes), n1 - n0)
