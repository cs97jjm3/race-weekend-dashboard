"""Gets timing data in, either live from F1 or replayed from the free archive."""
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request

LIVE_TOPICS = [
    "Heartbeat", "DriverList", "SessionInfo", "SessionStatus", "TrackStatus",
    "LapCount", "TimingData", "TimingAppData", "TimingStats",
    "RaceControlMessages", "WeatherData", "ExtrapolatedClock", "TopThree", "TeamRadio", "SessionData",
]
REPLAY_TOPICS = [
    "DriverList", "SessionInfo", "SessionStatus", "TrackStatus", "LapCount",
    "TimingData", "TimingAppData", "RaceControlMessages", "WeatherData", "TeamRadio", "SessionData",
    "TimingStats",
]
STATIC = "https://livetiming.formula1.com/static/"
UA = {"User-Agent": "race-weekend-dashboard/1.0"}
log = logging.getLogger("dashboard")


class ArchiveLocked(Exception):
    pass


def _get(url):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8-sig")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise ArchiveLocked(url)
        raise


class Recorder:
    """Saves everything that comes in live, in the same format as F1's archive,
    so any session can be replayed later with --folder."""

    def __init__(self, base):
        self.base = base
        self.folder = None
        self.start = None
        self.files = {}

    def __call__(self, topic, data):
        if self.folder is None:
            self.start = time.time()
            self.folder = os.path.join(self.base, time.strftime("%Y-%m-%d_%H%M"))
            os.makedirs(self.folder, exist_ok=True)
        if topic not in self.files:
            self.files[topic] = open(os.path.join(self.folder, topic + ".jsonStream"), "a", encoding="utf-8")
        s = time.time() - self.start
        line = "%02d:%02d:%06.3f%s\n" % (s // 3600, s % 3600 // 60, s % 60, json.dumps(data))
        f = self.files[topic]
        f.write(line)
        f.flush()


# ---------- live ----------

class LiveFeed:
    """F1's live timing stream. Only the free topics are asked for."""

    url = "wss://livetiming.formula1.com/signalrcore"
    negotiate = "https://livetiming.formula1.com/signalrcore/negotiate"

    def __init__(self, on_data, on_status, recorder=None):
        self.on_data = on_data
        self.on_status = on_status
        self.recorder = recorder
        self.conn = None
        self.last_msg = time.time()

    def _feed(self, msg):
        self.last_msg = time.time()
        try:
            topic, data, ts = msg[0], msg[1], msg[2] if len(msg) > 2 else None
            self._record(topic, data)
            self.on_data(topic, data, ts)
        except Exception:
            log.exception("Bad feed message")

    def _snapshot(self, msg):
        self.last_msg = time.time()
        result = getattr(msg, "result", None) or {}
        for topic, data in result.items():
            self._record(topic, data)
            self.on_data(topic, data, None)

    def _record(self, topic, data):
        if self.recorder and topic in REPLAY_TOPICS:
            try:
                self.recorder(topic, data)
            except Exception as e:
                log.warning("Couldn't save to recording: %s", e)

    def _connect(self):
        import requests
        from signalrcore.hub_connection_builder import HubConnectionBuilder

        r = requests.options(self.negotiate, headers=UA, timeout=20)
        headers = dict(UA)
        if "AWSALBCORS" in r.cookies:
            headers["Cookie"] = "AWSALBCORS=" + r.cookies["AWSALBCORS"]
        self.conn = (HubConnectionBuilder()
                     .with_url(self.url, options={"verify_ssl": True,
                                                  "access_token_factory": None,
                                                  "headers": headers})
                     .build())
        opened = []
        self.conn.on_open(lambda: opened.append(True))
        self.conn.on_close(lambda: self.on_status("Connection closed"))
        self.conn.on("feed", self._feed)
        self.conn.start()
        t0 = time.time()
        while not opened:
            if time.time() - t0 > 20:
                raise RuntimeError("Timed out connecting to F1 live timing")
            time.sleep(0.1)
        self.on_status("Connected to F1 live timing")
        self.conn.send("Subscribe", [LIVE_TOPICS], on_invocation=self._snapshot)
        self.last_msg = time.time()

    def run(self):
        while True:
            try:
                self._connect()
                # Heartbeat arrives regularly during a session; reconnect if it goes quiet
                while time.time() - self.last_msg < 120:
                    time.sleep(2)
                self.on_status("No data for 2 minutes, reconnecting")
            except Exception as e:
                self.on_status("Live timing problem: %s. Retrying in 15s" % e)
            try:
                if self.conn:
                    self.conn.stop()
            except Exception:
                pass
            time.sleep(15)


# ---------- replay ----------

_TS = re.compile(r"^(\d+):(\d\d):(\d\d)\.(\d+)")


def _parse_stream(text, topic):
    """Turn a .jsonStream file into (seconds, topic, data) entries."""
    out = []
    for line in text.splitlines():
        m = _TS.match(line)
        if not m:
            continue
        h, mi, s, frac = m.groups()
        t = int(h) * 3600 + int(mi) * 60 + int(s) + float("0." + frac)
        try:
            out.append((t, topic, json.loads(line[m.end():])))
        except ValueError:
            pass
    return out


def find_session(year, meeting, session):
    """Find a session's archive folder, e.g. (2026, 'Singapore', 'Race')."""
    idx = json.loads(_get("%s%s/Index.json" % (STATIC, year)))
    meeting_l, session_l = meeting.lower(), session.lower()
    for m in idx.get("Meetings", []):
        names = " ".join(str(m.get(k, "")) for k in ("Name", "OfficialName", "Location")).lower()
        if meeting_l not in names:
            continue
        for s in m.get("Sessions", []):
            if session_l == str(s.get("Name", "")).lower() and s.get("Path"):
                return s["Path"]
        found = ", ".join(s.get("Name", "?") for s in m.get("Sessions", []))
        raise SystemExit("Found %s but no session called '%s'. It has: %s"
                         % (m.get("Name"), session, found))
    raise SystemExit("Couldn't find a %s meeting matching '%s'" % (year, meeting))


class ReplayFeed:
    """Plays a past session back as if it were live, sped up if you like."""

    def __init__(self, on_data, on_status, speed=10.0, path=None, folder=None, start_lap=0, clock=None):
        self.clock = clock
        self.on_data = on_data
        self.on_status = on_status
        self.speed = max(0.1, speed)
        self.path = path
        self.folder = folder
        self.start_lap = start_lap

    def _load(self):
        entries = []
        for topic in REPLAY_TOPICS:
            try:
                if self.folder:
                    f = os.path.join(self.folder, topic + ".jsonStream")
                    if not os.path.exists(f):
                        continue
                    with open(f, encoding="utf-8-sig") as fh:
                        text = fh.read()
                else:
                    text = _get(STATIC + self.path + topic + ".jsonStream")
            except Exception as e:
                self.on_status("Couldn't load %s (%s), carrying on without it" % (topic, e))
                continue
            entries += _parse_stream(text, topic)
        entries.sort(key=lambda e: e[0])
        return entries

    def run(self):
        self.on_status("Loading replay data...")
        entries = self._load()
        if not entries:
            self.on_status("No replay data found")
            return
        # Skip ahead quickly to the chosen lap, then play in real (sped up) time
        skip_until = None
        if self.start_lap:
            for t, topic, data in entries:
                if topic == "LapCount" and isinstance(data, dict) and int(data.get("CurrentLap", 0) or 0) >= self.start_lap:
                    skip_until = t
                    break
        self.on_status("Replaying at %gx speed" % self.speed)
        prev = None
        for t, topic, data in entries:
            fast = skip_until is not None and t < skip_until
            if prev is not None and not fast:
                gap = (t - prev) / self.speed
                if gap > 0:
                    time.sleep(min(gap, 5))
            prev = t
            if self.clock:
                self.clock(t, self.speed)
            self.on_data(topic, data, None, quiet=fast)
        self.on_status("Replay finished")
