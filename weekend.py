"""The race weekend timetable and the weather outlook, for the screen between sessions.

The timetable comes from Jolpica's calendar (the same free service as the standings), the
outlook from Open-Meteo's forecast for the circuit at the start time of the next session.
"""
import json
import logging
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import history

log = logging.getLogger("dashboard")
NAMES = [("FirstPractice", "Practice 1", 60), ("SecondPractice", "Practice 2", 60), ("ThirdPractice", "Practice 3", 60),
         ("SprintQualifying", "Sprint Qualifying", 45), ("Sprint", "Sprint", 60), ("Qualifying", "Qualifying", 60),
         (None, "Race", 120)]
_outlook = {"at": 0.0, "key": None, "data": None}


def _when(d):
    if not d or not d.get("date"):
        return None
    t = (d.get("time") or "12:00:00Z").replace("Z", "")
    return datetime.fromisoformat("%sT%s" % (d["date"], t)).replace(tzinfo=timezone.utc)


def sessions(race):
    out = []
    for key, name, mins in NAMES:
        start = _when(race if key is None else race.get(key))
        if start:
            out.append({"name": name, "start": start, "end": start + timedelta(minutes=mins)})
    out.sort(key=lambda s: s["start"])
    return out


def this_weekend(races, now):
    """The weekend that's on now, or the next one if we're between weekends."""
    for r in races:
        ss = sessions(r)
        if ss and ss[-1]["end"] + timedelta(hours=3) > now:
            return r, ss
    return None, []


def outlook(lat, lon, when):
    """Forecast at the circuit for the hour a session starts: temperature, chance of rain, wind."""
    key = (lat, lon, when.strftime("%Y-%m-%dT%H"))
    if _outlook["key"] == key and time.time() - _outlook["at"] < 1800:
        return _outlook["data"]
    data = None
    if when - datetime.now(timezone.utc) < timedelta(days=15):
        url = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&hourly=temperature_2m,"
               "precipitation_probability,wind_speed_10m&wind_speed_unit=mph&timezone=UTC&forecast_days=16" % (lat, lon))
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "race-weekend-dashboard"}),
                                        timeout=10) as r:
                d = json.loads(r.read())
            h = d.get("hourly") or {}
            stamp = when.strftime("%Y-%m-%dT%H:00")
            if stamp in (h.get("time") or []):
                i = h["time"].index(stamp)
                data = {"temp": h["temperature_2m"][i], "rain": h["precipitation_probability"][i],
                        "wind": h["wind_speed_10m"][i]}
        except Exception as e:
            log.info("No weather outlook (%s)", e)
    _outlook.update(at=time.time(), key=key, data=data)
    return data


def view(now=None):
    """Everything the between-sessions screen needs."""
    now = now or datetime.now(timezone.utc)
    try:
        races = history._get("current/races.json", 6 * 3600)["MRData"]["RaceTable"]["Races"]
    except Exception as e:
        return {"error": "Couldn't get the calendar just now (%s)" % e}
    race, ss = this_weekend(races, now)
    if not race:
        return {"error": "No more races on this season's calendar."}
    nxt = None
    rows = []
    for s in ss:
        status = "done" if s["end"] <= now else "live" if s["start"] <= now else "next"
        if status != "done" and nxt is None:
            nxt = s
        rows.append({"name": s["name"], "start": s["start"].isoformat(), "status": status})
    loc = race["Circuit"]["Location"]
    out = {"name": race["raceName"], "round": race.get("round"), "circuit": race["Circuit"].get("circuitName"),
           "place": "%s, %s" % (loc.get("locality", ""), loc.get("country", "")), "sessions": rows}
    if nxt:
        out["next"] = {"name": nxt["name"], "start": nxt["start"].isoformat(),
                       "live": nxt["start"] <= now}
        out["outlook"] = outlook(loc.get("lat"), loc.get("long"), nxt["start"].replace(minute=0, second=0))
    return out
