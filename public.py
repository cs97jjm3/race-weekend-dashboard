"""The public live page: the dashboard for anyone, at its own web address.

When PUBLIC_HOSTNAME is set in config.py (for example "pitwall.jmmcp.uk") and that address is
pointed at the same Cloudflare tunnel as the family link, visitors to it get a read-only
version of the screen, with no passcode:

  - everything about the race: timing, map, tyres, gaps, lap chart, pit wall, head to head,
    stewards, FIA summaries, radio transcripts, the AI commentary, standings and the weekend
  - none of the family: no names, no who-follows-who, no predictions, no AI buttons, nothing
    that can be changed, and no radio audio (just the words)
  - visitors pick their own drivers on the page, and that's kept in their own browser

It's the same feed delay as at home, so it matches the telly. The screen is worked out at most
once a second however many people are watching, and sent compressed, so a home broadband
connection can cope with a fair few visitors.
"""
import copy
import gzip
import json
import re
import threading
import time

import config

DROP_KINDS = {"ai", "debrief"}            # asked for by the family, about the family
EXACT = ("/", "/index.html", "/state", "/weekend", "/favicon.ico")
PREFIXES = ("/track/", "/h2h?", "/static/")


def hostname():
    return (getattr(config, "PUBLIC_HOSTNAME", "") or "").strip().lower()


def is_public(handler):
    want = hostname()
    if not want:
        return False
    hosts = [handler.headers.get("Host") or "", handler.headers.get("X-Forwarded-Host") or ""]
    return any(h.split(":")[0].strip().lower() == want for h in hosts)


def allowed(path):
    """Only the screen and what it needs to draw itself; nothing that belongs to the family."""
    path = path.split("#")[0]
    base = path.split("?")[0]
    return base in EXACT or any(path.startswith(p) for p in PREFIXES)


def tidy_event(e):
    """One feed entry with the family taken out, or None to leave it off."""
    if e.get("kind") in DROP_KINDS:
        return None
    e = dict(e)
    e["who"] = []
    for k in ("audio", "radioNote", "ntfy", "link_private"):
        e.pop(k, None)
    if e.get("kind") == "finish":
        # "Chequered flag. Leclerc wins. James's lot: ..." -> just the result
        e["text"] = ". ".join(e.get("text", "").split(". ")[:2]).rstrip(".") + "."
    blob = " ".join(str(e.get(k) or "") for k in ("text", "title", "transcript", "say"))
    if any(re.search(r"\b%s\b" % re.escape(p), blob) for p in getattr(config, "PEOPLE", {})):
        return None                       # belt and braces: anything naming the family stays at home
    return e


def sanitise(v, race):
    """The family's screen, made fit for anyone."""
    v = copy.deepcopy(v)
    v["viewer"] = "public"
    v["people"] = {}
    v.pop("share", None)
    v["ai"] = {"available": False, "on": False, "commentary": False}
    for r in v.get("rows", []):
        r["watchers"] = []
    v["events"] = [x for x in (tidy_event(e) for e in v.get("events", [])) if x]
    v["radioClips"] = {}
    # the gap chart and pit wall cover everyone, so visitors can follow whoever they like
    gaps = {}
    for n, h in dict(race.gap_hist).items():
        if h:
            gaps[n] = sorted([lap, iv, gl] for lap, (iv, gl) in dict(h).items())
    v["gaps"] = gaps
    if v.get("isRace"):
        try:
            v["pitWall"] = race.pit_wall(v["rows"], everyone=True)
        except Exception:
            v["pitWall"] = []
    for c in v.get("pitWall") or []:
        c["watchers"] = []
    st = v.get("standings") or {}
    for d in st.get("drivers") or []:
        d["watchers"] = []
    for t in st.get("teams") or []:
        t["watchers"] = []
    for c in ((st.get("title") or {}).get("contenders") or []):
        c["watchers"] = []
    return v


def tidy_h2h(d):
    d = copy.deepcopy(d)
    for side in ("a", "b"):
        if isinstance(d.get(side), dict):
            d[side]["watchers"] = []
    return d


class Cache:
    """Works the public screen out at most once a second and keeps it compressed."""

    def __init__(self, race, every=1.0):
        self.race, self.every = race, every
        self.lock = threading.Lock()
        self.at = 0.0
        self.plain = self.packed = b"{}"

    def get(self):
        with self.lock:
            if time.time() - self.at >= self.every:
                v = sanitise(self.race.view(), self.race)
                self.plain = json.dumps(v, separators=(",", ":")).encode("utf-8")
                self.packed = gzip.compress(self.plain, 5)
                self.at = time.time()
            return self.plain, self.packed
