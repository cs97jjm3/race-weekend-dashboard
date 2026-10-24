"""AI commentary: short reads of the session on a chosen topic, on demand or every few laps.

The dashboard works out the facts (who's closing on whom and how fast, who's on old tyres,
where a driver would rejoin after a cheap pit stop under a safety car) and the AI only turns
them into a couple of sentences. That keeps the commentary to what the timing actually shows:
we know which tyre and how old it is, but not wear or temperatures, so it must never guess those.
"""
import logging
import threading
import time

import config
from race import gap_seconds, lap_seconds

log = logging.getLogger("dashboard")

# what each kind of session can talk about, in menu order
TOPICS = {
    "race": [("summary", "Race so far"), ("tyres", "Tyres"), ("catching", "Who's catching who"),
             ("pits", "Pit stops: who won and lost"), ("safetycar", "Safety car now?"), ("weather", "Weather")],
    "quali": [("summary", "Qualifying so far"), ("cut", "The cut line"), ("weather", "Weather")],
    "practice": [("summary", "Pace so far"), ("tyres", "Tyres and long runs"), ("weather", "Weather")],
}
LABEL = {"summary": "SO FAR", "tyres": "TYRES", "catching": "CATCHING", "safetycar": "SAFETY CAR?",
         "cut": "THE CUT", "pits": "PIT STOPS", "weather": "WEATHER"}
_forecast_cache = {"at": 0.0, "key": None, "data": None}
SC_FACTOR = 0.55        # a stop under the safety car costs roughly half the usual time
RULES = ("You're a calm, plain-spoken F1 commentator talking to a family watching together. {people}. "
         "Write 2 or 3 short sentences, like a TV commentator would say them. Use ONLY the facts below: "
         "don't invent tyre wear, temperatures, team radio, strategy plans or anything not given. "
         "Where it fits, mention the family's drivers by name. No hype words, no emojis, no bullet points, "
         "no lists, and don't start with 'So' or 'Well'.")


def who_follows(race):
    by_code = {d.get("Tla"): race.name(n) for n, d in race.drivers().items()}
    return "; ".join("%s follows %s" % (name, ", ".join(p["teams"] + [by_code.get(c, c) for c in p["drivers"]]))
                     for name, p in config.PEOPLE.items())


def kind(race):
    return "race" if race.is_race() else "quali" if race.is_quali() else "practice"


def _rows(race):
    return [r for r in race.view()["rows"] if r["pos"] < 99]


def _mine(r):
    return " (followed by %s)" % " and ".join(r["watchers"]) if r["watchers"] else ""


def _closing(race, rows, laps=4):
    """Pairs where the car behind has been taking time out of the car ahead over the last few laps."""
    out = []
    by_num = {r["num"]: r for r in rows}
    order = sorted((r for r in rows if not r["out"] and not r["inPit"]), key=lambda r: r["pos"])
    stopped_lately = {r["num"] for r in rows if (r["tyreAge"] or 99) <= laps}   # their gap jumps around the stop
    for i, r in enumerate(order[1:], 1):
        h = race.gap_hist.get(r["num"]) or {}
        if len(h) < 3 or r["num"] in stopped_lately or order[i - 1]["num"] in stopped_lately:
            continue
        recent = sorted(h.items())[-(laps + 1):]
        (lap0, (iv0, _)), (lap1, (iv1, _)) = recent[0], recent[-1]
        if iv0 is None or iv1 is None or lap1 <= lap0:
            continue
        rate = (iv0 - iv1) / (lap1 - lap0)          # seconds a lap the gap is shrinking
        if rate < 0.1 or rate > 2.0 or iv1 > 12 or iv1 < 0.05:
            continue                                 # too slow to matter, a pit stop in the sums, or no gap yet
        ahead = order[i - 1]
        laps_to = iv1 / rate if iv1 > 1.0 else 0
        out.append({"chaser": r, "ahead": ahead, "gap": iv1, "rate": rate, "laps": laps_to,
                    "mine": bool(r["watchers"] or by_num[ahead["num"]]["watchers"])})
    out.sort(key=lambda c: (not c["mine"], c["laps"]))
    return out


def _sc_rejoin(race, rows, r):
    """Where a car would come out after a stop under the safety car, from the current gaps."""
    me = gap_seconds(r["gap"]) if r["pos"] > 1 else 0.0
    if me is None:
        return None
    target = me + race.pit_loss() * SC_FACTOR
    ahead = [x for x in rows if x["num"] != r["num"] and not x["out"] and not x["inPit"]
             and (gap_seconds(x["gap"]) if x["pos"] > 1 else 0.0) is not None
             and (gap_seconds(x["gap"]) if x["pos"] > 1 else 0.0) < target]
    return len(ahead) + 1


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def forecast(race):
    """Chance of rain over the next hour or so at the circuit, from Open-Meteo (free, no key).
    Kept for ten minutes so the commentary doesn't keep asking."""
    where = race.coords()
    if not where:
        return None
    key = tuple(where)
    if _forecast_cache["key"] == key and time.time() - _forecast_cache["at"] < 600:
        return _forecast_cache["data"]
    import json
    import urllib.request
    url = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&minutely_15=precipitation"
           "&forecast_minutely_15=6&hourly=precipitation_probability&forecast_hours=2&timezone=auto" % where)
    data = None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "race-weekend-dashboard"}), timeout=10) as r:
            d = json.loads(r.read())
        mm = [x or 0 for x in (d.get("minutely_15") or {}).get("precipitation") or []]
        pr = [x or 0 for x in (d.get("hourly") or {}).get("precipitation_probability") or []]
        data = {"chance": max(pr[:2]) if pr else None, "mm": round(sum(mm[:4]), 1) if mm else None}
    except Exception as e:
        log.info("No rain forecast for the commentary (%s)", e)
    _forecast_cache.update(at=time.time(), key=key, data=data)
    return data


def weather_story(race):
    """What the weather is doing and how much it has changed since the start: (facts, how much it matters 0-3)."""
    w, w0 = race.raw.get("WeatherData") or {}, race.weather_start or {}
    air, track, hum, wind = _num(w.get("AirTemp")), _num(w.get("TrackTemp")), _num(w.get("Humidity")), _num(w.get("WindSpeed"))
    raining = str(w.get("Rainfall", "0")) not in ("0", "", "None")
    lines, matters = [], 0.0
    if air is not None:
        lines.append("Now: air %.0fC, track %.0fC%s%s, %s." % (
            air, track or 0, ", humidity %.0f%%" % hum if hum is not None else "",
            ", wind %.0f mph" % (wind * 2.237) if wind is not None else "", "raining at the track" if raining else "dry"))
    t0 = _num(w0.get("TrackTemp"))
    if t0 is not None and track is not None:
        change = track - t0
        lines.append("Track temperature at the start: %.0fC, so it has %s by %.0f degrees." % (
            t0, "warmed up" if change > 0 else "cooled", abs(change)) if abs(change) >= 1 else
            "Track temperature is about the same as at the start (%.0fC)." % t0)
        if abs(change) >= 6:
            matters += 1.5
    was_wet = str(w0.get("Rainfall", "0")) not in ("0", "", "None")
    if raining:
        matters += 3 if not was_wet else 1.5
    f = forecast(race)
    if f and f.get("chance") is not None:
        lines.append("Forecast for the circuit: %d%% chance of rain in the next hour%s." % (
            f["chance"], ", about %.1f mm expected" % f["mm"] if f.get("mm") else ""))
        if f["chance"] >= 40:
            matters += 1.5 + (f["chance"] - 40) / 30
    if not lines:
        lines.append("No weather readings from the track yet.")
    lines.append("Cooler track usually means the tyres take longer to warm up; a hotter one wears them faster. "
                 "Rain means a switch to intermediate or wet tyres.")
    return lines, matters


def pit_story(race, rows):
    """Each stop, where they came out and who they gained on or lost to (undercuts)."""
    by_num = {r["num"]: r for r in rows}
    stops = [s for s in race.pit_log if s["num"] in by_num]
    stopped_laps = {}
    for s in stops:
        stopped_laps.setdefault(s["num"], []).append(s["lap"] or 0)
    out = []
    for s in stops[-12:]:
        r = by_num[s["num"]]
        now = r["pos"]
        line = "%s%s pitted on lap %s from P%d (on %s-lap-old %s), now P%d" % (
            r["name"], _mine(r), s["lap"], s["pos"], s["age"] if s["age"] is not None else "?",
            (s["tyre"] or "unknown").lower(), now)
        if s.get("ahead") and s["ahead"] in by_num:
            a = by_num[s["ahead"]]
            their = stopped_laps.get(a["num"], [])
            a_stopped = any(l > (s["lap"] or 0) for l in their)
            if (s["lap"] or 0) in their:
                line += "; %s, who was just ahead, stopped on the same lap and is now P%d" % (a["name"], a["pos"])
            elif a_stopped:
                line += "; it %s %s, who was ahead and stopped later" % (
                    "got them past" if now < a["pos"] else "didn't get them past", a["name"])
            elif not a["out"]:
                line += "; %s, who was just ahead, hasn't stopped since" % a["name"]
        out.append(line)
    gains = []
    for num, laps_ in stopped_laps.items():
        first = next(s for s in stops if s["num"] == num)
        gains.append((first["pos"] - by_num[num]["pos"], by_num[num]["name"]))
    gains.sort()
    if gains:
        if gains[-1][0] > 0:
            out.append("Most places gained since their first stop: %s (+%d)" % (gains[-1][1], gains[-1][0]))
        if gains[0][0] < 0:
            out.append("Most places lost since their first stop: %s (%d)" % (gains[0][1], gains[0][0]))
    yet = [r["name"] for r in rows if not r["out"] and not r["stops"]]
    if yet:
        out.append("Still to stop: " + ", ".join(yet))
    return out


def facts(race, topic):
    """The numbers for one topic, as plain lines of text."""
    v = race.view()
    rows = _rows(race)
    k = kind(race)
    head = "%s %s" % (v["meeting"], v["session"])
    if k == "race":
        head += ", lap %s of %s" % (v["lap"], v["total"])
    head += ". Track: %s." % (v["trackText"] or "green")
    lines = [head]
    live = [r for r in rows if not r["out"]]

    if topic == "summary" and k == "race":
        grid = getattr(race, "grid", {}) or {}
        for r in live[:10] + [r for r in live[10:] if r["watchers"]]:
            g = grid.get(r["num"])
            moved = (" (started P%d)" % g) if g and g != r["pos"] else ""
            lines.append("P%d %s, %s%s%s, %s stop(s)" % (r["pos"], r["name"], r["team"], moved, _mine(r), r["stops"] or 0))
        out = [r["name"] for r in rows if r["out"]]
        if out:
            lines.append("Out of the race: " + ", ".join(out))
        recent = [e["text"] for e in v["events"][:8] if e.get("kind") not in ("ai", "comm")]
        if recent:
            lines.append("Recent updates, newest first: " + " | ".join(recent))

    elif topic == "tyres":
        for r in live:
            if k != "race" and not (r["watchers"] or r["pos"] <= 10):
                continue
            stints = r.get("stints") or []
            used = " then ".join("%s %d laps" % (s["c"].lower(), s["laps"]) for s in stints if s["c"])
            last, best = lap_seconds(r["last"]), lap_seconds(r.get("best"))
            fade = (", last lap %.1fs off their best" % (last - best)) if last and best and last - best > 0.8 and k == "race" else ""
            lines.append("P%d %s%s: now on %s, %s laps old; %s stop(s) so far (sets used: %s)%s" % (
                r["pos"], r["name"], _mine(r), (r["tyre"] or "unknown").lower(), r["tyreAge"] if r["tyreAge"] is not None else "?",
                r["stops"] or 0, used or "unknown", fade))
        if k == "race":
            none = [r["name"] for r in live if not r["stops"]]
            if none:
                lines.append("Not stopped yet: " + ", ".join(none))
        else:
            lines.append("In practice, several laps in a row on one set is a long run (race pace); short runs on softs are for one fast lap.")

    elif topic == "catching":
        pairs = _closing(race, rows)
        if not pairs:
            lines.append("Nobody is closing on the car ahead by more than a tenth a lap right now.")
        for c in pairs[:5]:
            when = "already within a second" if c["gap"] <= 1.0 else "would catch in about %d laps at this rate" % max(1, round(c["laps"]))
            lines.append("%s (P%d)%s is %.1fs behind %s (P%d)%s, closing %.2fs a lap, %s" % (
                c["chaser"]["name"], c["chaser"]["pos"], _mine(c["chaser"]), c["gap"], c["ahead"]["name"],
                c["ahead"]["pos"], _mine(c["ahead"]), c["rate"], when))
        if v["lap"] and v["total"]:
            lines.append("Laps left: %d" % (int(v["total"]) - int(v["lap"])))

    elif topic == "safetycar":
        lines.append("A pit stop here normally costs about %d seconds; under a safety car about %d." % (
            race.pit_loss(), round(race.pit_loss() * SC_FACTOR)))
        for r in [x for x in live if x["watchers"] or x["pos"] <= 5]:
            p = _sc_rejoin(race, rows, r)
            if p is None:
                continue
            lines.append("%s is P%d%s on %s-lap-old %s, %s stop(s): a stop under a safety car now would drop them to about P%d" % (
                r["name"], r["pos"], _mine(r), r["tyreAge"] if r["tyreAge"] is not None else "?",
                (r["tyre"] or "unknown").lower(), r["stops"] or 0, p))
        lines.append("(Everyone else might stop too, which would change it; this is a what-if.)")

    elif topic == "cut" and k == "quali":
        q = v["quali"] or {}
        cut = q.get("cutAfter")
        lines.append("Q%s. %s" % (q.get("part"), ("The top %d go through." % cut) if cut else "This is the shoot-out for pole."))
        for r in live:
            if r.get("knockedOut"):
                continue
            near = cut and abs(r["pos"] - cut) <= 3
            if near or r["watchers"] or r["pos"] <= 3:
                tc = r.get("toCut")
                cutx = (", %+.3fs to the cut" % tc) if tc is not None and cut else ""
                lines.append("P%d %s%s, best %s%s%s" % (r["pos"], r["name"], _mine(r), r.get("partBest") or "no time yet",
                                                        cutx, ", in the pits" if r["inPit"] else ", on track"))
        ko = [r["name"] for r in rows if r.get("knockedOut")]
        if ko:
            lines.append("Already knocked out: " + ", ".join(ko))

    elif topic == "weather":
        w_lines, _ = weather_story(race)
        lines += w_lines
        if k == "race":
            lines.append("Tyres now: " + ", ".join("%s %s" % (r["name"], (r["tyre"] or "?").lower())
                                                    for r in live[:6] + [r for r in live[6:] if r["watchers"]]))

    elif topic == "pits" and k == "race":
        story = pit_story(race, rows)
        lines += story or ["Nobody has stopped yet."]
        lines.append("A pit stop here costs about %d seconds." % race.pit_loss())
        lines.append("An undercut is stopping before the car ahead to use fresh tyres to get past when they stop.")

    else:   # summary for qualifying and practice
        for r in live[:10] + [r for r in live[10:] if r["watchers"]]:
            best = r.get("partBest") if k == "quali" else r.get("best")
            lines.append("P%d %s, %s%s, best %s%s" % (r["pos"], r["name"], r["team"], _mine(r), best or "no time yet",
                                                      ", %s off the top" % r["gap"] if r["gap"] and r["pos"] > 1 else ""))
        if k == "quali" and v["quali"]:
            lines.append("Part: Q%s" % v["quali"]["part"])
        recent = [e["text"] for e in v["events"][:6] if e.get("kind") not in ("ai", "comm")]
        if recent:
            lines.append("Recent updates, newest first: " + " | ".join(recent))
    return "\n".join(lines)


def pick(race, last=None):
    """The most interesting topic right now, never the same one twice running."""
    k = kind(race)
    _, wet = weather_story(race)
    if k == "quali":
        choice = (["weather"] if wet >= 2 else []) + ["cut", "summary"]
    elif k == "practice":
        choice = (["weather"] if wet >= 2 else []) + ["summary", "tyres"]
    else:
        rows = _rows(race)
        live = [r for r in rows if not r["out"]]
        lap, total = race.lap()
        score = {"summary": 1.0}
        pairs = _closing(race, rows)
        if pairs:
            score["catching"] = 2.0 + (1.5 if pairs[0]["mine"] else 0) + (1 if pairs[0]["laps"] < 6 else 0)
        old = [r for r in live if (r["tyreAge"] or 0) >= 15]
        unstopped = [r for r in live if not r["stops"]]
        if lap and total and int(lap) > 8 and (len(old) >= 4 or unstopped):
            score["tyres"] = 1.5 + min(len(old), 8) / 4
        if lap and total and 5 < int(lap) < int(total) - 5:
            score["safetycar"] = 1.3
        if wet >= 2:
            score["weather"] = 2.0 + wet            # rain coming or here is the biggest story there is
        recent = [x for x in race.pit_log if x["lap"] and lap and int(lap) - int(x["lap"]) <= 6]
        if len(recent) >= 2 and len(race.pit_log) >= 3:
            score["pits"] = 2.2 + min(len(recent), 6) / 6
        choice = sorted(score, key=lambda t: -score[t])
    for t in choice:
        if t != last:
            return t
    return choice[0]


def read(race, topic=None, last=None):
    """Ask the AI for a read on one topic. Returns (topic, text)."""
    from notify import ask_claude
    topic = topic or pick(race, last)
    allowed = [t for t, _ in TOPICS[kind(race)]]
    if topic not in allowed:
        topic = allowed[0]
    data = facts(race, topic)          # view() takes the race lock itself
    prompt = (RULES.format(people=who_follows(race)) + "\n\nTopic: %s.\n\n%s" % (
        dict(TOPICS[kind(race)])[topic], data))
    return topic, ask_claude(prompt, max_tokens=1500)


class Commentator:
    """Every 5 laps or so in a race (less often in practice), adds a read to the feed on its own."""
    EVERY_LAPS = 5
    MIN_GAP = 240            # never closer than 4 minutes apart
    PRACTICE_GAP = 8 * 60
    QUALI_GAP = 5 * 60

    def __init__(self, race, emit):
        self.race, self.emit = race, emit
        self.on = bool(getattr(config, "AI_COMMENTARY", True))
        self.last_topic, self.last_time, self.last_lap = None, 0.0, None
        threading.Thread(target=self._loop, daemon=True).start()

    def due(self):
        r = self.race
        from notify import ai_ready
        if not (self.on and ai_ready() and getattr(r.notify, "enabled", False)):
            return False
        if r.session_status != "Started" or r.track in ("4", "5", "6", "7") or r.quiet:
            return False                     # nothing on, or a safety car / red flag is the story
        now = time.time()
        speed = r.replay_speed or 1      # replays run faster, so the commentary does too
        if now - self.last_time < self.MIN_GAP / speed:
            return False
        k = kind(r)
        if k == "race":
            lap = r.lap()[0]
            if not lap:
                return False
            if self.last_lap is None:
                return int(lap) >= 3
            return int(lap) - self.last_lap >= self.EVERY_LAPS
        return now - self.last_time >= (self.QUALI_GAP if k == "quali" else self.PRACTICE_GAP) / speed

    def _loop(self):
        while True:
            time.sleep(15 if not self.race.replay_speed else 2)
            try:
                if self.due():
                    self.say()
            except Exception as e:
                log.info("Commentary skipped (%s)", e)
                self.last_time = time.time()     # don't hammer the AI if it's failing

    def say(self, topic=None):
        self.last_time = time.time()
        lap = self.race.lap()[0]
        self.last_lap = int(lap) if lap else None
        topic, text = read(self.race, topic, self.last_topic)
        self.last_topic = topic
        self.emit(topic, text)
        return topic, text
