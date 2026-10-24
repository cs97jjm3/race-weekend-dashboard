"""Stewards' decisions published as FIA documents.

Many decisions (especially grid penalties after qualifying) are only published as PDFs on the
FIA's website, never in the timing feed. Five minutes before each session, and once when the
dashboard starts, this reads the FIA's list of documents for the current event and puts any
new decisions, infringements, summonses and penalties in the feed, with a link to the PDF.
Only the list is read (a few times a day), never the PDFs themselves.
"""
import json
import logging
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

log = logging.getLogger("dashboard")
LIST_URL = "https://www.fia.com/documents/championships/fia-formula-one-world-championship-14"
HERE = os.path.dirname(os.path.abspath(__file__))
SEEN_FILE = os.path.join(HERE, "history_cache", "fia_seen.json")
KINDS = ("decision", "infringement", "offence", "penalty", "reprimand", "summons", "summon")
WORDS = {"pu": "PU", "drs": "DRS", "fp": "FP", "fp1": "FP1", "fp2": "FP2", "fp3": "FP3", "sq": "SQ", "q1": "Q1", "q2": "Q2", "q3": "Q3", "fia": "FIA"}


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def documents(html, event_name, city="", year=None):
    """The PDF links on the FIA page that belong to this event, newest first, as (url, title).
    The FIA names files after the event ("2026 United States Grand Prix - ...") or sometimes
    after the city ("2026 Melbourne Event - ..."), so either will do."""
    wants = [_slug(event_name)] + (["%s_event" % _slug(city)] if city else [])
    year = str(year or datetime.now(timezone.utc).year)
    out, seen = [], set()
    for m in re.finditer(r'href="([^"]*?/decision-document/[^"]+?\.pdf)"', html, re.I):
        url = urllib.parse.urljoin("https://www.fia.com/", m.group(1).replace("&amp;", "&").replace(" ", "%20"))
        name = urllib.parse.unquote(url.rsplit("/", 1)[-1])[:-4]
        slug = _slug(name)
        if not slug.startswith(year) or not any(w in slug for w in wants) or url in seen:
            continue
        seen.add(url)
        out.append((url, title(name, event_name, city)))
    return out


def title(name, event_name, city=""):
    """'2026_united_states_grand_prix_-_decision_-_car_63_-_alleged_forcing...' -> 'Decision - Car 63 - Alleged forcing...'"""
    t = re.sub(r"_+", " ", name)
    t = re.sub(r"^\d{4}\s+", "", t)
    for prefix in (event_name, "%s event" % city if city else None):
        if prefix:
            t = re.sub(r"^" + re.escape(prefix.lower()) + r"\s*-\s*", "", t, flags=re.I)
    t = re.sub(r"\s*-\s*", " - ", t).strip(" -")
    parts = [p.strip() for p in t.split(" - ") if p.strip()]
    nice = []
    for p in parts:
        words = [WORDS.get(w.lower(), w) for w in p.split()]
        s = " ".join(words)
        nice.append(s[:1].upper() + s[1:])
    return " - ".join(nice)


def is_decision(t):
    first = t.split(" - ")[0].lower()
    return any(first.startswith(k) for k in KINDS)


class Checker:
    """Looks at the FIA's documents five minutes before each session and once at start-up."""

    def __init__(self, race, emit, fetch=None):
        self.race, self.emit = race, emit
        self.fetch = fetch or self._fetch
        self.done = set()           # session start times already checked
        self.seen = self._load()
        threading.Thread(target=self._loop, daemon=True).start()

    def _load(self):
        try:
            with open(SEEN_FILE, encoding="utf-8") as f:
                return set(json.load(f))
        except (OSError, ValueError):
            return set()

    def _save(self):
        try:
            os.makedirs(os.path.dirname(SEEN_FILE), exist_ok=True)
            with open(SEEN_FILE, "w", encoding="utf-8") as f:
                json.dump(sorted(self.seen), f)
        except OSError:
            pass

    @staticmethod
    def _fetch():
        req = urllib.request.Request(LIST_URL, headers={"User-Agent": "Mozilla/5.0 (race-weekend-dashboard)"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode("utf-8", "replace")

    def check(self, event_name, city=""):
        """Read the list once; post anything new. Returns how many were posted."""
        try:
            html = self.fetch()
        except Exception as e:
            log.info("Couldn't read the FIA documents (%s)", e)
            return 0
        docs = [(u, t) for u, t in documents(html, event_name, city) if is_decision(t)]
        new = [(u, t) for u, t in docs if u not in self.seen]
        for url, t in reversed(new):         # oldest first, so the newest ends up on top of the feed
            self.emit(t, url)
            self.seen.add(url)
        if new:
            self._save()
            log.info("FIA documents: %d new decision(s)", len(new))
        return len(new)

    def _loop(self):
        import weekend
        time.sleep(90)
        first = True
        while True:
            try:
                w = weekend.view()
                name = w.get("name")
                if name:
                    now = datetime.now(timezone.utc)
                    due = first
                    for s in w.get("sessions", []):
                        start = datetime.fromisoformat(s["start"])
                        if start not in self.done and timedelta(0) <= start - now <= timedelta(minutes=5):
                            self.done.add(start)
                            due = True
                    if due:
                        self.check(name, (w.get("place") or "").split(",")[0])
                        first = False
            except Exception as e:
                log.info("FIA check skipped (%s)", e)
            time.sleep(60)


def car_names(race, text):
    """'Car 44' -> 'Car 44 (Hamilton)', and who in the family it matters to."""
    who = set()

    def name(m):
        n = m.group(1)
        if n in race.drivers():
            who.update(race.watchers(n))
            return "Car %s (%s)" % (n, race.name(n))
        return m.group(0)
    return re.sub(r"\bCar (\d+)\b", name, text, flags=re.I), who
