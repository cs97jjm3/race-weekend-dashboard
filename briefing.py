"""The race morning briefing: what you need to know before lights out, in a few sentences.

Built from things the dashboard already has: the weekend timetable and weather outlook,
qualifying from Jolpica, grid penalties from the FIA decisions it read over the weekend, and
the championship. No AI, so it costs nothing and never makes anything up. Moby's Home can
read it out on the Echos on race morning; the Weekend tab shows it too.
"""
import logging
from datetime import datetime, timezone

import config
import fia
import history
import weekend

log = logging.getLogger("dashboard")
NUMBERS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
           "eleven", "twelve"]


def nice_time(dt):
    """A UTC time as the laptop's local time, said the way people say it: '5pm', '1:30pm'."""
    local = dt.astimezone()
    h, m = local.hour, local.minute
    suffix = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    return ("%d%s" % (h12, suffix)) if m == 0 else ("%d:%02d%s" % (h12, m, suffix))


def ordinal(n):
    n = int(n)
    return "%d%s" % (n, "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th"))


def followers(code, team):
    who = []
    for person, p in config.PEOPLE.items():
        if code in p.get("drivers", []) or any(t and t.lower() in (team or "").lower() for t in p.get("teams", [])):
            who.append(person)
    return who


def build(now=None, standings=None):
    now = now or datetime.now(timezone.utc)
    w = weekend.view(now)
    if w.get("error"):
        return {"error": w["error"]}
    race = next((x for x in w["sessions"] if x["name"] == "Race"), None)
    if not race:
        return {"error": "No race on the calendar this weekend"}
    start = datetime.fromisoformat(race["start"])
    local_today = now.astimezone().date()
    race_day = start.astimezone().date() == local_today and race["status"] != "done"
    title = w["name"]
    lines, said = [], []

    when = nice_time(start)
    if race_day:
        said.append("Good morning. It's race day at the %s." % title)
        said.append("Lights out at %s." % when)
    else:
        said.append("The %s is on %s at %s." % (title, start.astimezone().strftime("%A"), when))
    lines.append("Lights out: %s" % start.astimezone().strftime("%A %H:%M").replace(" 0", " "))

    # qualifying
    grid = []
    try:
        q = history._get("current/%s/qualifying.json" % w["round"], 1800)["MRData"]["RaceTable"]["Races"]
        grid = (q[0].get("QualifyingResults") if q else []) or []
    except Exception as e:
        log.info("Briefing: no qualifying result yet (%s)", e)
    if grid:
        name = lambda r: r["Driver"].get("familyName", "?")
        top = grid[:3]
        said.append("%s is on pole%s." % (name(top[0]),
                    (", with %s and %s behind" % (name(top[1]), name(top[2]))) if len(top) == 3 else ""))
        lines.append("Front of the grid: " + ", ".join("P%s %s" % (r["position"], name(r)) for r in top))
        mine = []
        for r in grid:
            code, team = r["Driver"].get("code", ""), (r.get("Constructor") or {}).get("name", "")
            if followers(code, team) and r not in top:
                mine.append("%s %s" % (name(r), ordinal(r["position"])))
        if mine:
            said.append("Starting further back: " + ", ".join(mine[:5]) + ".")
            lines.append("Yours: " + ", ".join(mine))
    else:
        lines.append("Qualifying result not out yet")

    # grid penalties from the FIA decisions read this weekend
    pens = fia.grid_penalties(title)
    if pens:
        bits = []
        for num, who, dec in pens[:4]:
            short = dec.split(" for the ")[0].replace("Drop of ", "").rstrip(".")
            bits.append("%s, %s" % (who.split()[-1] if who else "car " + num, short.lower()))
        said.append("Grid penalties: " + "; ".join(bits) + ".")
        lines.append("Grid penalties: " + "; ".join(bits))

    # weather at the start
    o = w.get("outlook") if (w.get("next") or {}).get("name") == "Race" else None
    if o:
        said.append("The forecast for the start is %d degrees, with a %d percent chance of rain." % (
            round(o["temp"]), round(o["rain"])))
        lines.append("Weather at the start: %d°C, %d%% chance of rain, wind %d mph" % (
            round(o["temp"]), round(o["rain"]), round(o["wind"])))

    # championship
    data = getattr(standings, "data", None) or {}
    drivers = sorted(data.get("drivers") or [], key=lambda d: -d["points"])
    if len(drivers) >= 2:
        a, b = drivers[0], drivers[1]
        gap = int(a["points"] - b["points"])
        said.append("In the championship, %s leads %s by %d point%s." % (a["name"], b["name"], gap, "" if gap == 1 else "s"))
        lines.append("Championship: %s leads %s by %d" % (a["name"], b["name"], gap))

    return {"raceDay": race_day, "title": title, "start": race["start"], "lines": lines, "say": " ".join(said)}
