"""Keeps track of the session and works out what's worth telling you about."""
import re
import threading
import time

import config

TRACK = {
    "1": ("GREEN", "Green flag"),
    "2": ("YELLOW", "Yellow flag"),
    "4": ("SC", "Safety car"),
    "5": ("RED", "Red flag"),
    "6": ("VSC", "Virtual safety car"),
    "7": ("VSC_END", "VSC ending"),
}
EVERYONE = tuple(config.PEOPLE)


def merge(base, upd):
    """F1 sends small changes; fold them into the full picture."""
    if isinstance(upd, dict):
        if isinstance(base, list):
            for k, v in upd.items():
                if k == "_deleted":
                    continue
                try:
                    i = int(k)
                except ValueError:
                    continue
                while len(base) <= i:
                    base.append(None)
                base[i] = merge(base[i], v)
            return base
        if not isinstance(base, dict):
            base = {}
        for k in upd.get("_deleted", []) if isinstance(upd.get("_deleted"), list) else []:
            base.pop(str(k), None)
        for k, v in upd.items():
            if k != "_deleted":
                base[k] = merge(base.get(k), v)
        return base
    return upd


def gap_seconds(text):
    """'+5.123' -> 5.123, leader -> 0.0, lapped or unknown -> None."""
    if text is None:
        return None
    t = str(text).strip()
    if t == "" or t.upper().startswith("LAP"):
        return 0.0
    m = re.match(r"^\+?(\d+(?:\.\d+)?)$", t)
    return float(m.group(1)) if m else None


def lap_seconds(text):
    m = re.match(r"^(?:(\d+):)?(\d+(?:\.\d+)?)$", str(text or "").strip())
    if not m:
        return None
    return int(m.group(1) or 0) * 60 + float(m.group(2))


def items(x):
    if isinstance(x, dict):
        return [v for _, v in sorted(x.items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 0)]
    return [v for v in (x or []) if v is not None]


def by_index(x, i):
    """Item i of something F1 sends either as a list or as {"0": .., "1": ..}."""
    if isinstance(x, dict):
        return x.get(str(i))
    if isinstance(x, list) and i < len(x):
        return x[i]
    return None


class Race:
    def __init__(self, notifier):
        self.notify = notifier
        self.lock = threading.Lock()
        self.raw = {}
        self.events = []
        self.status = "Starting up"
        self.prev = {}            # last seen per-driver values
        self.leader = None
        self.track = "1"
        self.session_status = None
        self.rcm_seen = 0
        self.cooldown = {}
        self.pending_moves = {}   # num -> (new_pos, old_pos, since)
        self.finished_sent = False
        self.quiet = False
        self.radio_seen = 0
        self.sector_flags = {}    # marshal sector number -> "YELLOW" / "DOUBLE YELLOW"
        self.radio_hook = None    # set by the radio transcriber, if installed
        self.finish_hooks = []    # called once when a race or sprint finishes (predictions, debrief)
        self.replay_speed = None  # set when replaying
        self.sim_t = None
        self.sim_wall = None
        self.last_data = None     # wall clock time of the last message
        self.crossed = {}         # num -> (clock time it last crossed the line, lap time in seconds)
        self.gap_hist = {}        # num -> {lap: (interval to car ahead, gap to leader)}
        self.top_speed = {}       # num -> best speed trap reading, km/h
        self.quali_part = 0       # last qualifying part we've seen
        self.penalties = {}       # num -> list of penalty labels this session, e.g. ["+5s"]
        self.standings = None     # set by dashboard.py to the Standings helper

    def set_clock(self, t, speed):
        """Replays tell us where we are in the session, so the map can keep time."""
        self.sim_t, self.sim_wall, self.replay_speed = t, time.time(), speed

    def now(self):
        if self.sim_t is None:
            return time.time()
        return self.sim_t + (time.time() - self.sim_wall) * self.replay_speed

    # ----- helpers -----

    def drivers(self):
        # the live feed slips a marker ("_kf": true) in among the drivers, so keep only real driver entries
        return {k: v for k, v in (self.raw.get("DriverList") or {}).items() if isinstance(v, dict)}

    def name(self, num):
        d = self.drivers().get(str(num)) or {}
        return d.get("LastName") or d.get("BroadcastName") or d.get("Tla") or "#%s" % num

    def tla(self, num):
        return (self.drivers().get(str(num)) or {}).get("Tla", "")

    def watchers(self, num):
        d = self.drivers().get(str(num)) or {}
        team, tla = str(d.get("TeamName", "")).lower(), d.get("Tla", "")
        out = []
        for person, p in config.PEOPLE.items():
            if tla in p["drivers"] or any(t.lower() in team for t in p["teams"] if t):
                out.append(person)
        return tuple(out)

    def lines(self):
        return {k: v for k, v in ((self.raw.get("TimingData") or {}).get("Lines") or {}).items() if isinstance(v, dict)}

    def session(self):
        return self.raw.get("SessionInfo") or {}

    def session_type(self):
        return str(self.session().get("Type", "")).lower()

    def is_race(self):
        return self.session_type() in ("race", "sprint")

    def is_quali(self):
        t = self.session_type()
        name = str(self.session().get("Name", "")).lower()
        return "qualifying" in t or "shootout" in t or "qualifying" in name or "shootout" in name

    def is_sprint(self):
        return self.session_type() == "sprint" or "sprint" == str(self.session().get("Name", "")).lower()

    def lap(self):
        lc = self.raw.get("LapCount") or {}
        return lc.get("CurrentLap"), lc.get("TotalLaps")

    def stints(self, num):
        app = ((self.raw.get("TimingAppData") or {}).get("Lines") or {}).get(str(num)) or {}
        return items(app.get("Stints"))

    def tyre(self, num):
        stints = self.stints(num)
        if not stints:
            return None, None
        s = stints[-1]
        return s.get("Compound"), s.get("TotalLaps")

    def coords(self):
        loc = str((self.session().get("Meeting") or {}).get("Location", ""))
        for k, v in getattr(config, "CIRCUIT_COORDS", {}).items():
            if loc and k.lower() in loc.lower():
                return list(v)
        return None

    def pit_loss(self):
        loc = str((self.session().get("Meeting") or {}).get("Location", ""))
        for k, v in config.PIT_LOSS_SECONDS.items():
            if k.lower() in loc.lower():
                return v
        return config.DEFAULT_PIT_LOSS

    def classification(self):
        """Current running order, retired cars left out."""
        order = sorted(((int(l.get("Position") or 99), n) for n, l in self.lines().items()
                        if not (l.get("Retired") or l.get("Stopped"))))
        return [n for p, n in order if p < 99]

    def emit(self, text, who=EVERYONE, title="Race Weekend", kind="info", key=None, loud=False, wait=None,
             show=True, send=True, extra=None):
        if key:
            now = self.now()
            if now - self.cooldown.get(key, -1e12) < (wait or config.ALERT_COOLDOWN_SECONDS):
                return None
            self.cooldown[key] = now
        lap, total = self.lap()
        if self.is_quali():
            lap = None
        ev = {"t": time.strftime("%H:%M:%S"), "lap": lap, "text": text, "who": list(who),
              "kind": kind, "title": title, "shown": show, "ntfy": send}
        if self.is_quali() and self.quali_part:
            ev["part"] = "Q%d" % self.quali_part
        if extra:
            ev.update(extra)
        if show:
            self.events.insert(0, ev)
            del self.events[300:]
        if not self.quiet:
            self.notify(ev, loud)
        return ev

    # ----- incoming data -----

    def update(self, topic, data, ts=None, quiet=False):
        self.last_data = time.time()
        if topic.endswith(".z") or topic == "Heartbeat":
            return
        with self.lock:
            self.quiet = quiet
            if topic == "SessionInfo" and isinstance(data, dict) and data.get("Path"):
                old_path = (self.raw.get("SessionInfo") or {}).get("Path")
                if old_path and old_path != data["Path"]:
                    self._new_session()
            self.raw[topic] = merge(self.raw.get(topic), data)
            try:
                if topic == "TrackStatus":
                    self._track()
                elif topic == "SessionStatus":
                    self._session_status()
                elif topic == "RaceControlMessages":
                    self._rcm()
                elif topic == "TimingData":
                    self._timing()
                elif topic == "TimingStats":
                    self._stats()
                elif topic == "SessionData":
                    self._session_data()
                elif topic == "TeamRadio":
                    self._radio()
                self._settle_moves()
            except Exception as e:
                self.status = "Problem reading %s: %s" % (topic, e)

    def _new_session(self):
        """F1 has moved on to the next session (e.g. FP1 to FP2): start the timing afresh,
        so nothing from the last session hangs about. The feed history is kept."""
        keep = {k: self.raw[k] for k in ("DriverList", "WeatherData", "TrackStatus") if k in self.raw}
        self.raw = keep
        self.prev, self.leader, self.session_status = {}, None, None
        self.rcm_seen = self.radio_seen = 0
        self.cooldown, self.pending_moves, self.sector_flags = {}, {}, {}
        self.finished_sent = False
        self.crossed, self.gap_hist, self.top_speed, self.penalties = {}, {}, {}, {}
        self.quali_part = 0

    def _track(self):
        st = str((self.raw.get("TrackStatus") or {}).get("Status", "1"))
        if st == "1":
            self.sector_flags.clear()
        if st == self.track:
            return
        old, self.track = self.track, st
        if st == "4":
            self.emit("Safety car deployed", title="SAFETY CAR", kind="sc", loud=True)
        elif st == "5":
            self.emit("Red flag, session stopped", title="RED FLAG", kind="red", loud=True)
        elif st == "6":
            self.emit("Virtual safety car deployed", title="VSC", kind="sc", loud=True)
        elif st == "1" and old in ("4", "5", "6", "7"):
            self.emit("Back to green", title="GREEN FLAG", kind="green")

    def _session_status(self):
        st = (self.raw.get("SessionStatus") or {}).get("Status")
        if st == self.session_status:
            return
        self.session_status = st
        name = "%s %s" % ((self.session().get("Meeting") or {}).get("Name", ""), self.session().get("Name", ""))
        if st == "Started":
            if self.is_quali() and self.quali_part > 1:
                return     # each part restarting; the part change already said so
            self.emit("%s is underway" % name.strip(), title="LIGHTS OUT" if self.is_race() else "SESSION STARTED", kind="start")
        elif st in ("Finished", "Finalised") and not self.finished_sent:
            if self.is_quali() and self.quali_part and self.quali_part < self._last_part():
                return     # just the end of Q1 or Q2
            self.finished_sent = True
            self._summary()

    def _summary(self):
        order = self.classification()
        winner = self.name(order[0]) if order else "?"
        if self.is_race():
            lead = "Chequered flag. %s wins." % winner
        elif self.is_quali():
            lead = "Qualifying over. %s on pole." % winner
        else:
            lead = "Session over. %s fastest." % winner
        parts = []
        for person in config.PEOPLE:
            mine = ["P%d %s" % (i + 1, self.name(n)) for i, n in enumerate(order) if person in self.watchers(n)]
            text = "%s Your lot: %s" % (lead, ", ".join(mine) or "none classified")
            self.emit(text, who=(person,), title="CHEQUERED FLAG", kind="finish", loud=True, show=False)
            parts.append("%s's lot: %s." % (person, ", ".join(mine) or "none classified"))
        self.emit(lead + " " + " ".join(parts), title="CHEQUERED FLAG", kind="finish", send=False)
        if self.is_race() and not self.quiet:
            for hook in self.finish_hooks:
                try:
                    hook(order)
                except Exception as e:
                    self.status = "Problem after the finish: %s" % e

    def _radio(self):
        """A new team radio clip has been published. Only your drivers' ones go in the feed."""
        caps = items((self.raw.get("TeamRadio") or {}).get("Captures"))
        new, self.radio_seen = caps[self.radio_seen:], len(caps)
        for c in new:
            n = str(c.get("RacingNumber", ""))
            who = self.watchers(n)
            if who and c.get("Path"):
                ev = self.emit("%s is on the radio" % self.name(n), who=who, title="TEAM RADIO", kind="radio",
                               send=False, extra={"audio": c["Path"]})
                if ev and self.radio_hook and not self.quiet:
                    self.radio_hook(ev)

    def _flag(self, m):
        """Keep track of yellow flags in individual marshal sectors, for the map."""
        flag = str(m.get("Flag", "")).upper()
        scope = str(m.get("Scope", "")).capitalize()
        if scope == "Sector" and m.get("Sector") is not None:
            sec = int(m["Sector"])
            if flag in ("YELLOW", "DOUBLE YELLOW"):
                new = sec not in self.sector_flags
                self.sector_flags[sec] = flag
                if new:
                    self.emit("%s flag in marshal sector %d" % (flag.capitalize(), sec), title="YELLOW FLAG",
                              kind="yellow", send=False, key="sector-%d" % sec)
            elif flag in ("CLEAR", "GREEN"):
                self.sector_flags.pop(sec, None)
        elif scope == "Track" and flag in ("GREEN", "CLEAR", "CHEQUERED"):
            self.sector_flags.clear()

    def _rcm(self):
        msgs = items((self.raw.get("RaceControlMessages") or {}).get("Messages"))
        new, self.rcm_seen = msgs[self.rcm_seen:], len(msgs)
        for m in new:
            self._flag(m)
            text = str(m.get("Message", ""))
            nums = set(re.findall(r"CAR (\d+)", text)) | set(re.findall(r"\b(\d+) \([A-Z]{3}\)", text))
            who = set()
            for n in nums:
                who.update(self.watchers(n))
            up = text.upper()
            tidy = re.sub(r"(?:cars? )?(\d+) \([a-z]{3}\)", lambda m: self.name(m.group(1)), text.lower())
            tidy = re.sub(r"\bfia\b", "FIA", tidy)
            tidy = re.sub(r"^FIA stewards:\s*", "", tidy)
            tidy = tidy[:1].upper() + tidy[1:]
            stewards = "STEWARDS" in up or any(w in up for w in ("PENALTY", "INVESTIGATION", "NOTED", "REPRIMAND",
                                                                  "BLACK AND WHITE", "DISQUALIFIED"))
            if stewards and "DELETED" not in up:
                self._penalty(nums, up)
                stage = ("PENALTY" if "PENALTY" in up and "NO PENALTY" not in up and "SERVED" not in up
                         else "SERVED" if "SERVED" in up
                         else "NO FURTHER ACTION" if "NO FURTHER" in up or "NO PENALTY" in up
                         else "UNDER INVESTIGATION" if "UNDER INVESTIGATION" in up
                         else "NOTED" if "NOTED" in up
                         else "REPRIMAND" if "REPRIMAND" in up
                         else "DECISION")
                # stewards' calls go to everyone; it's tagged to whoever follows the drivers involved
                self.emit(tidy, who=tuple(who) if who else EVERYONE, title="STEWARDS · " + stage, kind="stewards",
                          loud=bool(who) and stage == "PENALTY")
            elif who and "DELETED" in up:
                self.emit(tidy, who=tuple(who), title="RACE CONTROL", kind="rc")

    def _penalty(self, nums, up):
        """Keep a short note of penalties so the timing table can show them."""
        if len(nums) != 1:
            m = re.search(r"PENALTY FOR CAR (\d+)", up)
            nums = {m.group(1)} if m else set()
        for n in nums:
            pens = self.penalties.setdefault(n, [])
            if "SERVED" in up:
                pens[:] = [p for p in pens if p not in ("DT", "SG")]
                continue
            if "NO PENALTY" in up or "NO FURTHER" in up:
                continue
            m = re.search(r"(\d+) SECOND TIME PENALTY", up)
            if m:
                pens.append("+%ss" % m.group(1))
            elif "DRIVE THROUGH" in up:
                pens.append("DT")
            elif "STOP/GO" in up or "STOP GO" in up:
                pens.append("SG")
            elif "GRID" in up and "PENALTY" in up:
                g = re.search(r"(\d+) PLACE GRID", up)
                pens.append("%s grid" % ("-" + g.group(1) if g else ""))
            elif "BLACK AND WHITE" in up:
                pens.append("B/W flag")
            elif "DISQUALIFIED" in up:
                pens.append("DSQ")

    def _stats(self):
        """TimingStats carries each driver's best speed trap reading."""
        for n, l in ((self.raw.get("TimingStats") or {}).get("Lines") or {}).items():
            v = (((l or {}).get("BestSpeeds") or {}).get("ST") or {}).get("Value")
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            if v > self.top_speed.get(n, 0):
                self.top_speed[n] = v

    # ----- qualifying -----

    def _part_now(self):
        series = items((self.raw.get("SessionData") or {}).get("Series"))
        for s in reversed(series):
            if s and s.get("QualifyingPart"):
                return int(s["QualifyingPart"])
        part = (self.raw.get("TimingData") or {}).get("SessionPart")
        return int(part) if part else 1

    def _entries(self):
        """How many cars start each part, e.g. [22, 16, 10]."""
        ne = (self.raw.get("TimingData") or {}).get("NoEntries")
        ne = [int(x) for x in items(ne)] if ne else []
        if len(ne) >= 2:
            return ne
        n = len(self.lines()) or len(self.drivers()) or 20
        return [n, 16, 10] if n >= 22 else [n, 15, 10]

    def _last_part(self):
        return len(self._entries())

    def _cut_after(self):
        """Positions above this go through to the next part (None in the last part)."""
        part, ne = self.quali_part or 1, self._entries()
        return ne[part] if part < len(ne) else None

    def _part_best(self, l, part=None):
        b = by_index(l.get("BestLapTimes"), (part or self.quali_part or 1) - 1) or {}
        return lap_seconds(b.get("Value"))

    def _session_data(self):
        part = self._part_now()
        if part == self.quali_part:
            return
        old, self.quali_part = self.quali_part, part
        if not self.is_quali() or not old:
            return
        through = [n for n, l in self.lines().items() if not l.get("KnockedOut") and not l.get("Retired")]
        for person in config.PEOPLE:
            mine = [self.name(n) for n in through if person in self.watchers(n)]
            if mine:
                self.emit("Through to Q%d: %s" % (part, ", ".join(mine)), who=(person,),
                          title="THROUGH", kind="up", key="through-%s-%d" % (person, part))
        self.emit("Q%d is underway" % part, title="SESSION STARTED", kind="start", send=False)

    def _quali(self, n, l, who, p):
        """Drop-zone alerts for your drivers."""
        if not who or not self.quali_part:
            return
        if l.get("KnockedOut"):
            if not p.get("ko"):
                p["ko"] = True
                pos = l.get("Position", "?")
                self.emit("%s is out in Q%d, P%s" % (self.name(n), self.quali_part, pos), who=who,
                          title="KNOCKED OUT", kind="out", loud=True)
            return
        cut = self._cut_after()
        best = self._part_best(l)
        try:
            pos = int(l.get("Position") or 0)
        except ValueError:
            return
        if not cut or not pos or best is None or self.session_status not in ("Started", None):
            p["zone"] = None
            return
        zone = pos > cut
        if zone == p.get("zone"):
            return
        was, p["zone"] = p.get("zone"), zone
        line_car = self._at_position(cut)
        line_best = self._part_best(self.lines().get(line_car) or {}) if line_car else None
        if zone:
            off = (" %.3f off P%d" % (best - line_best, cut)) if line_best else ""
            self.emit("%s is in the drop zone, P%d,%s" % (self.name(n), pos, off), who=who,
                      title="DROP ZONE", kind="down", key="zone-%s-%d" % (n, self.quali_part), wait=45)
        elif was:
            self.emit("%s is out of the drop zone, up to P%d" % (self.name(n), pos), who=who,
                      title="SAFE FOR NOW", kind="up", key="safe-%s-%d" % (n, self.quali_part), wait=45)

    # ----- timing -----

    def _timing(self):
        lines = self.lines()
        race = self.is_race()
        quali = self.is_quali()
        if quali and not self.quali_part:
            self.quali_part = self._part_now()
        # new leader
        for n, l in lines.items():
            if str(l.get("Position")) == "1" and race:
                if self.leader and n != self.leader and not (lines.get(self.leader, {}).get("InPit")):
                    self.emit("%s takes the lead from %s" % (self.name(n), self.name(self.leader)),
                              title="NEW LEADER", kind="lead", key="lead")
                self.leader = n
        for n, l in lines.items():
            p = self.prev.setdefault(n, {})
            who = self.watchers(n)
            pos = l.get("Position")
            # speed trap
            st = ((l.get("Speeds") or {}).get("ST") or {}).get("Value")
            try:
                st = float(st)
                if st > self.top_speed.get(n, 0):
                    self.top_speed[n] = st
            except (TypeError, ValueError):
                pass
            # pit stops
            in_pit = bool(l.get("InPit"))
            if in_pit and not p.get("in_pit"):
                p["pitted_at"] = self.now()
                if who and race:
                    self._pit_alert(n, who)
            p["in_pit"] = in_pit
            # retirements
            out = bool(l.get("Retired")) or bool(l.get("Stopped"))
            if out and not p.get("out") and race and (who or str(p.get("pos")) in ("1", "2", "3")):
                self.emit("%s is out of the race" % self.name(n),
                          who=who or EVERYONE, title="RETIRED", kind="out", loud=bool(who))
            p["out"] = out
            # position changes (settled after a few seconds to ignore pit-cycle flicker)
            if pos and p.get("pos") and pos != p.get("pos") and who and race:
                old = self.pending_moves.get(n, (None, p["pos"], 0))[1]
                self.pending_moves[n] = (pos, old, self.now())
            p["pos"] = pos
            # line crossings, for the map
            last = l.get("LastLapTime") or {}
            lv = last.get("Value")
            if lv and lv != p.get("lastlap"):
                secs = lap_seconds(lv)
                if secs:
                    self.crossed[n] = (self.now(), secs)
                p["lastlap"] = lv
            # gap history for the chart: the latest gaps seen on each lap
            if race:
                laps_done = l.get("NumberOfLaps")
                iv = gap_seconds((l.get("IntervalToPositionAhead") or {}).get("Value"))
                gl = gap_seconds(l.get("GapToLeader"))
                if str(pos) == "1":
                    iv = None      # leading: there's no car ahead to measure to
                if laps_done and (iv is not None or gl is not None) and not out:
                    self.gap_hist.setdefault(n, {})[int(laps_done)] = (iv, gl)
            # fastest lap
            if last.get("OverallFastest") and lv and lv != p.get("fl") and who:
                p["fl"] = lv
                what = "fastest lap of the race" if race else "fastest time of the session"
                if quali:
                    what = "fastest time in Q%d" % (self.quali_part or 1)
                self.emit("%s sets the %s: %s" % (self.name(n), what, lv),
                          who=who, title="FASTEST", kind="fast", key="fl-%s-%s" % (n, lv))
            # battles: only when a car first closes to within striking distance of the one ahead
            iv = gap_seconds(((l.get("IntervalToPositionAhead") or {}).get("Value")))
            if race and who and iv is not None and pos and pos != "1" and not in_pit:
                ahead = self._at_position(int(pos) - 1)
                close = 0 < iv <= config.BATTLE_GAP
                was_close = p.get("battle") == ahead
                if close and not was_close and ahead and (lap_now := self.lap()[0]) and int(lap_now) > 1:
                    self.emit("%s has closed to %.1fs behind %s, fighting for P%s" % (self.name(n), iv, self.name(ahead), int(pos) - 1),
                              who=who, title="BATTLE", kind="battle", key="battle-%s-%s" % (n, ahead), wait=600)
                if close:
                    p["battle"] = ahead
                elif iv > config.BATTLE_GAP + 0.5:
                    p["battle"] = None
            if quali:
                self._quali(n, l, who, p)

    def _settle_moves(self):
        now = self.now()
        lines = self.lines()
        for n, (new, old, since) in list(self.pending_moves.items()):
            if now - since < (0 if self.quiet else 8):
                continue
            del self.pending_moves[n]
            cur = (lines.get(n) or {}).get("Position")
            if cur != new or cur == old or (lines.get(n) or {}).get("InPit") or (lines.get(n) or {}).get("PitOut"):
                continue
            if now - self.prev.get(n, {}).get("pitted_at", -1e9) < 150:
                continue  # the pit stop alert already said where they'd rejoin
            new_i, old_i = int(new), int(old)
            if 1 in (new_i, old_i):
                continue  # the new leader alert already covers this
            if new_i < old_i:
                passed = self._at_position(new_i + 1)
                extra = " (past %s)" % self.name(passed) if passed and new_i + 1 == old_i else ""
                self.emit("%s up to P%d%s" % (self.name(n), new_i, extra), who=self.watchers(n),
                          title="UP", kind="up", key="mv-%s-%s" % (n, new))
            else:
                self.emit("%s down to P%d" % (self.name(n), new_i), who=self.watchers(n),
                          title="DOWN", kind="down", key="mv-%s-%s" % (n, new))

    def _at_position(self, pos):
        for n, l in self.lines().items():
            if str(l.get("Position")) == str(pos):
                return n
        return None

    def _pit_alert(self, n, who):
        lines = self.lines()
        me = lines.get(n) or {}
        my_gap = gap_seconds(me.get("GapToLeader"))
        comp, age = self.tyre(n)
        tyre = (" on %s-lap-old %ss" % (age, comp.lower())) if comp and age is not None else ""
        text = "%s pits from P%s%s." % (self.name(n), me.get("Position", "?"), tyre)
        if my_gap is not None:
            target = my_gap + self.pit_loss()
            field = []
            for m, l in lines.items():
                if m == n or l.get("InPit") or l.get("Retired") or l.get("Stopped"):
                    continue
                g = gap_seconds(l.get("GapToLeader"))
                if g is not None:
                    field.append((g, m))
            field.sort()
            ahead = [m for g, m in field if g < target]
            behind = [m for g, m in field if g >= target]
            est = len(ahead) + 1
            text += " Should rejoin around P%d" % est
            if ahead:
                text += ", behind %s" % self.name(ahead[-1])
            if behind:
                text += ", ahead of %s" % self.name(behind[0])
            text += "."
        self.emit(text, who=who, title="PIT STOP", kind="pit", key="pit-%s-%s" % (n, me.get("NumberOfPitStops")), loud=True)

    def _progress(self, n, l):
        """Roughly how far round the lap a car is (0 to 1), from when it last crossed
        the line and how long its last lap took. An estimate, not GPS."""
        if l.get("Retired") or l.get("Stopped") or l.get("KnockedOut"):
            return None
        if l.get("InPit") and not self.is_race():
            return None
        c = self.crossed.get(n)
        if c:
            t0, secs = c
            done = (self.now() - t0) / secs
            if done > 1.6:
                return None     # not seen crossing the line for a while: probably in the garage
            return round(min(0.995, max(0.0, done)), 4)
        lap, _ = self.lap()
        pos = int(l.get("Position") or 0)
        if self.is_race() and pos and (not lap or int(lap) <= 1):
            return round(-0.006 * (pos - 1) % 1, 4)   # lined up on the grid
        return None

    def _rate(self, n):
        """How much of a lap the car covers per real second, so the screen can glide it."""
        c = self.crossed.get(n)
        if not c or not c[1]:
            return 0
        return round((self.replay_speed or 1) / c[1], 6)

    def _sectors(self, l):
        out = []
        for i in range(3):
            s = by_index(l.get("Sectors"), i) or {}
            v = s.get("Value") or ""
            c = "purple" if s.get("OverallFastest") else "green" if s.get("PersonalFastest") else ("yellow" if v else "")
            out.append({"v": v, "c": c})
        return out

    def _stint_list(self, n):
        out = []
        for s in self.stints(n):
            try:
                total = int(s.get("TotalLaps") or 0)
                start = int(s.get("StartLaps") or 0)
            except (TypeError, ValueError):
                total, start = 0, 0
            out.append({"c": s.get("Compound") or "", "laps": max(0, total - start), "new": s.get("New")})
        return out

    # ----- for the screen -----

    def view(self):
        with self.lock:
            quali = self.is_quali()
            cut = self._cut_after() if quali else None
            line_car = self._at_position(cut) if cut else None
            line_best = self._part_best(self.lines().get(line_car) or {}) if line_car else None
            rows = []
            for n, l in self.lines().items():
                d = self.drivers().get(n) or {}
                comp, age = self.tyre(n)
                last = l.get("LastLapTime") or {}
                row = {
                    "num": n, "pos": int(l.get("Position") or 99),
                    "tla": d.get("Tla", n), "name": self.name(n),
                    "team": d.get("TeamName", ""), "colour": "#" + str(d.get("TeamColour", "888888")).lstrip("#"),
                    "gap": l.get("GapToLeader") or (l.get("TimeDiffToFastest") or ""),
                    "interval": (l.get("IntervalToPositionAhead") or {}).get("Value") or (l.get("TimeDiffToPositionAhead") or ""),
                    "last": last.get("Value", ""), "lastFastest": bool(last.get("OverallFastest")),
                    "best": (l.get("BestLapTime") or {}).get("Value", ""),
                    "tyre": comp or "", "tyreAge": age, "stops": l.get("NumberOfPitStops", 0),
                    "inPit": bool(l.get("InPit")), "out": bool(l.get("Retired") or l.get("Stopped")),
                    "watchers": list(self.watchers(n)),
                    "progress": self._progress(n, l),
                    "rate": self._rate(n),
                    "sectors": self._sectors(l),
                    "topSpeed": self.top_speed.get(n),
                    "pens": self.penalties.get(n, []),
                    "stints": self._stint_list(n),
                }
                if quali:
                    best = self._part_best(l)
                    row["knockedOut"] = bool(l.get("KnockedOut"))
                    row["partBest"] = (by_index(l.get("BestLapTimes"), (self.quali_part or 1) - 1) or {}).get("Value", "")
                    if row["knockedOut"]:
                        for i in range((self.quali_part or 1) - 1, -1, -1):
                            v = (by_index(l.get("BestLapTimes"), i) or {}).get("Value")
                            if v:
                                row["partBest"] = v
                                break
                    row["toCut"] = round(best - line_best, 3) if (best is not None and line_best is not None
                                                                 and not row["knockedOut"]) else None
                rows.append(row)
            rows.sort(key=lambda r: (r["out"], r["pos"]))
            lap, total = self.lap()
            s = self.session()
            w = self.raw.get("WeatherData") or {}
            people = {k: v["colour"] for k, v in config.PEOPLE.items()}
            watched = {r["num"] for r in rows if r["watchers"]}
            gaps = {}
            for n in watched:
                h = self.gap_hist.get(n) or {}
                if h:
                    gaps[n] = sorted([lap_no, v[0], v[1]] for lap_no, v in h.items())
            view = {
                "meeting": (s.get("Meeting") or {}).get("Name", ""), "session": s.get("Name", ""),
                "sessionType": self.session_type(), "sessionStatus": self.session_status,
                "lap": lap, "total": total, "track": TRACK.get(self.track, ("GREEN", ""))[0],
                "trackText": TRACK.get(self.track, ("", ""))[1], "isRace": self.is_race(),
                "isQuali": quali, "isSprint": self.is_sprint(),
                "quali": {"part": self.quali_part or 1, "cutAfter": cut, "entries": self._entries()} if quali else None,
                "finished": self.finished_sent,
                "air": w.get("AirTemp"), "trackTemp": w.get("TrackTemp"), "rain": w.get("Rainfall"),
                "humidity": w.get("Humidity"), "windSpeed": w.get("WindSpeed"), "windDir": w.get("WindDirection"),
                "pressure": w.get("Pressure"), "location": (s.get("Meeting") or {}).get("Location", ""),
                "coords": self.coords(),
                "radiusMiles": getattr(config, "WEATHER_RADIUS_MILES", 5),
                "radioClips": self.radio_seen,
                "sectorFlags": {str(k): v for k, v in self.sector_flags.items()},
                "ai": {"available": bool(config.ANTHROPIC_API_KEY.strip()),
                       "on": bool(getattr(self.notify, "enabled", False))},
                "rows": rows, "events": self.events[:80], "status": self.status,
                "people": people,
                "gaps": gaps,
                "age": None if self.last_data is None else round(time.time() - self.last_data, 1),
                "replay": self.replay_speed,
                "circuit": ((s.get("Meeting") or {}).get("Circuit") or {}).get("Key"),
                "year": str(s.get("StartDate", "") or s.get("Path", "") or time.strftime("%Y"))[:4],
            }
        if self.standings:
            try:
                view["standings"] = self.standings.live(self, rows)
            except Exception as e:
                view["standings"] = {"error": str(e)}
        return view
