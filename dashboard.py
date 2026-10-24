"""Ed's Race Weekend Dashboard: a live race screen and phone alerts for the family.

Live (during a session):     py dashboard.py
Replay a past session:       py dashboard.py --replay 2026 Singapore Race --speed 20
Start a replay at a lap:     py dashboard.py --replay 2026 Singapore Race --from-lap 30
See it working with a made-up race:  py dashboard.py --demo
...or a made-up qualifying session:  py dashboard.py --demo quali
Replay a race you recorded:          py dashboard.py --folder recordings\2026-10-25_1955
Then open http://localhost:8080 in a browser.
"""
import argparse
import json
import re
import logging
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
import track
from feed import ArchiveLocked, LiveFeed, Recorder, ReplayFeed, find_session
from notify import Notifier, ai_name, ai_ready, provider, ai_model
from predictions import Predictions
from radio import Radio
from race import EVERYONE, Race
from standings import Standings
import share
import commentary
import fia
import weekend
import history

HERE = os.path.dirname(os.path.abspath(__file__))
logging.basicConfig(format="%(asctime)s %(message)s", level=logging.INFO)
log = logging.getLogger("dashboard")


ASK_GAP_SECONDS = 20
_last_ask = [0.0]


from commentary import who_follows   # noqa: E402  (kept here for older imports)


def debrief(race, preds):
    """After the flag: Claude writes a short debrief of the race from James's and Ed's side."""
    from notify import ask_claude
    with race.lock:
        order = race.classification()
        lines = []
        for i, n in enumerate(order):
            l = race.lines().get(n) or {}
            d = race.drivers().get(n) or {}
            fans = " (followed by %s)" % " and ".join(race.watchers(n)) if race.watchers(n) else ""
            lines.append("P%d %s, %s, %s pit stop(s)%s" % (i + 1, race.name(n), d.get("TeamName", ""),
                                                         l.get("NumberOfPitStops", 0), fans))
        for n, l in race.lines().items():
            if l.get("Retired") or l.get("Stopped"):
                lines.append("DNF %s, %s" % (race.name(n), (race.drivers().get(n) or {}).get("TeamName", "")))
        story = [("Lap %s: " % e["lap"] if e.get("lap") else "") + e["text"]
                 for e in reversed(race.events) if e["kind"] not in ("ai", "debrief", "radio")][-80:]
        name = "%s %s" % ((race.session().get("Meeting") or {}).get("Name", ""), race.session().get("Name", ""))
    p = preds.state()
    game = ""
    if p.get("scores"):
        game = "\nTheir podium predictions game: " + "; ".join(
            "%s picked %s and scored %d" % (who, ", ".join(p["picks"].get(who, [])), pts) for who, pts in p["scores"].items())
    prompt = ("Write a short debrief of the %s for James and his son Ed, two fans who watched it together. %s.\n"
              "Two short paragraphs, about 120 to 160 words in total: how the race was won, then how their drivers got on. "
              "Plain, warm British English, like a friendly race engineer. No hype, no emojis, no bullet points, no headings, "
              "no dashes as punctuation. Use ONLY the facts below and don't invent anything.\n\n"
              "Final order:\n%s\n\nWhat happened, in order:\n%s%s"
              % (name, who_follows(race), "\n".join(lines), "\n".join(story), game))
    text = ask_claude(prompt, max_tokens=2000)
    with race.lock:
        race.emit(text, title="RACE DEBRIEF" + via(), kind="debrief", send=False)
    return text


def check_ai(who, backup=False):
    """--test-ai: one hello to one AI, with a plain explanation if it fails."""
    from notify import _ask, _key, _ready
    setting = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY"}.get(who, "ANTHROPIC_API_KEY")
    head = "%s (%s, model %s)" % (ai_name(who), "back-up, AI_FALLBACK" if backup else "main, AI_PROVIDER", ai_model(who))
    if not _ready(who):
        return "%s: no key yet. Put it between the quotes after %s in config.py." % (head, setting)
    from notify import Busy
    try:
        reply = _ask(who, "In one short sentence, say hello to James and Ed and wish them a good race weekend.", 1000, 30)
    except Busy as e:
        return (head + ": the key is fine: %s accepted it, but it's busy right now (%s).\n"
                         "Try again in a few minutes%s.\n"
                         % (ai_name(who), e, ", or pick another model with GEMINI_MODEL in config.py, e.g. \"gemini-3.5-flash-lite\""
                            if who == "gemini" else ""))
    except Exception as e:
        k = _key(who)
        starts = {"gemini": "AQ. or AIza", "openai": "(depends on the service)"}.get(who, "sk-ant-api")
        where = {"gemini": "aistudio.google.com/apikey", "openai": "the service you're using"}.get(who, "the Claude Console")
        checks = ["Length: %d characters" % len(k),
                  "Starts with: %s (should be %s)" % (k[:4], starts),
                  "Ends with: ...%s (compare with the key's ending shown at %s)" % (k[-4:], where)]
        if " " in k:
            checks.append("Has a space in the middle: the paste has gone wrong somewhere")
        if "..." in k or "your key" in k.lower():
            checks.append("Still has the example text in it")
        hint = ""
        if who == "gemini" and ("location" in str(e).lower() or "region" in str(e).lower() or "billing" in str(e).lower()):
            hint = ("Google says the free tier isn't available here, or wants billing turned on. In the UK the\n"
                    "Gemini API may only be offered as a paid service. Switch back with AI_PROVIDER = \"claude\".\n")
        return (head + ": the key didn't work: %s\n\nWhat I can see of %s in config.py:\n  %s\n\n%s"
                         "401 or 403 usually means the key isn't recognised; make a fresh one at %s and paste it again.\n"
                         % (e, setting, "\n  ".join(checks), hint, where))
    return "%s: the key works. %s says: %s" % (head, ai_name(who), reply)


def via():
    """' · via Claude' on the title when the back-up AI answered instead of the usual one."""
    from notify import last_used, used_backup
    return " · via %s" % ai_name(last_used[0]) if used_backup() else ""


def make_handler(race, radio, preds, gate):
    def failed(e):
        """Claude didn't answer: say so in the cmd window and in the feed, not just on the button."""
        log.warning("'What's happening?' failed: %s", e)
        who = ai_name()[:1].upper() + ai_name()[1:]
        msg = ("Something went wrong on our side: %s. Please send James a screenshot." % e
               if isinstance(e, commentary.FactsProblem) else
               "%s didn't answer this time (%s). Try again in a minute." % (who, e))
        with race.lock:
            race.emit(msg,
                      title="AI", kind="info", send=False)
        return {"error": "%s didn't answer" % ai_name()}

    def say(topic, text, auto):
        """Put a read in the feed: 'COMMENTARY · TYRES' when it came by itself, 'TYRES' when asked for."""
        label = commentary.LABEL.get(topic, "READ")
        with race.lock:
            race.emit(text, title=("COMMENTARY · " if auto else "") + label + via(),
                      kind="comm" if auto else "ai", send=False)

    race.say = say

    def ask_now(topic=None):
        """The 'What's happening?' button and its menu: a short read on a topic (or the most
        interesting one right now). After the chequered flag it writes the race debrief instead."""
        if not ai_ready():
            return {"error": "No AI key in config.py"}
        if not race.notify.enabled:
            return {"error": "AI is switched off"}
        wait = ASK_GAP_SECONDS - (time.time() - _last_ask[0])
        if wait > 0:
            return {"error": "Give it %d more seconds" % wait}
        _last_ask[0] = time.time()
        if not race.lines():
            return {"error": "No race data yet"}
        if race.finished_sent and race.is_race() and topic in (None, "debrief"):
            try:
                debrief(race, preds)
                return {"ok": True}
            except Exception as e:
                return {"error": "%s didn't answer: %s" % (ai_name(), e)}
        try:
            last = race.commentator.last_topic if getattr(race, "commentator", None) else None
            t, reply = commentary.read(race, topic, last)
        except Exception as e:
            return failed(e)
        say(t, reply, auto=False)
        return {"ok": True}

    page = open(os.path.join(HERE, "dashboard.html"), encoding="utf-8").read().encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, data, code=200):
            body = json.dumps(data).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _page(self, html, code=200, extra=None):
            body = html.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        STATIC_TYPES = {".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
                        ".webmanifest": "application/manifest+json"}

        def _static(self):
            name = "favicon.ico" if self.path == "/favicon.ico" else self.path[len("/static/"):].split("?")[0]
            ext = os.path.splitext(name)[1]
            fn = os.path.join(HERE, "static", name)
            if "/" in name or "\\" in name or name.startswith(".") or ext not in self.STATIC_TYPES or not os.path.isfile(fn):
                return self.send_error(404)
            with open(fn, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", self.STATIC_TYPES[ext])
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(body)

        def _let_in(self):
            """Home Wi-Fi gets straight in; visitors from outside need the passcode."""
            if gate.allowed(self):
                return True
            if self.command == "GET" and self.path in ("/", "/index.html"):
                self._page(share.login_page(""))
            else:
                self._json({"error": "Passcode needed"}, 401)
            return False

        def do_POST(self):
            if self.path == "/login":
                n = int(self.headers.get("Content-Length") or 0)
                form = urllib.parse.parse_qs(self.rfile.read(min(n, 2000)).decode("utf-8", "replace"))
                if gate.locked_out(self):
                    return self._page(share.login_page("Too many wrong tries. Wait ten minutes and try again."), 429)
                cookie = gate.check(self, (form.get("code") or [""])[0])
                if not cookie:
                    return self._page(share.login_page("That code isn't right. Check the message from James."), 403)
                log.info("Someone came in with the passcode")
                self.send_response(303)
                self.send_header("Set-Cookie", cookie)
                self.send_header("Location", "/")
                self.end_headers()
                return
            if not self._let_in():
                return
            if self.path == "/ai/toggle" and gate.is_remote(self):
                return self._json({"error": "Only James can switch that"}, 403)
            if self.path == "/ai/toggle":
                if ai_ready():
                    race.notify.enabled = not race.notify.enabled
                    log.info("AI comments %s", "on" if race.notify.enabled else "off")
                return self._json({"on": race.notify.enabled})
            if self.path == "/ai/ask":
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    topic = (json.loads(self.rfile.read(min(n, 2000)) or b"{}") or {}).get("topic") if n else None
                except Exception:
                    topic = None
                return self._json(ask_now(topic if isinstance(topic, str) else None))
            if self.path == "/share/prepare":
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    text = str((json.loads(self.rfile.read(min(n, 20000)) or b"{}") or {}).get("text") or "")[:4000]
                except Exception:
                    return self._json({"error": "Couldn't read that"}, 400)
                try:
                    return self._json({"text": commentary.public_version(text)})
                except Exception as e:
                    log.info("Share version failed: %s", e)
                    return self._json({"error": "Couldn't take the names out just now (%s). You can edit them out by hand." % e,
                                       "text": text})
            if self.path == "/ai/commentary" and gate.is_remote(self):
                return self._json({"error": "Only James can switch that"}, 403)
            if self.path == "/ai/commentary":
                c = getattr(race, "commentator", None)
                if c:
                    c.on = not c.on
                    log.info("Commentary %s", "on" if c.on else "off")
                return self._json({"on": bool(c and c.on)})
            if self.path == "/predictions":
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(min(n, 10000)) or b"{}")
                except Exception:
                    return self._json({"error": "Couldn't read that"}, 400)
                return self._json(preds.pick(body.get("person"), body.get("picks")))
            self.send_error(404)

        def do_GET(self):
            if self.path.startswith("/login"):
                return self._page(share.login_page(""))
            if self.path == "/favicon.ico" or self.path.startswith("/static/"):
                return self._static()          # icons and the app details: fine to show without the passcode
            if not self._let_in():
                return
            if self.path.startswith("/state"):
                v = race.view()
                remote = gate.is_remote(self)
                v["viewer"] = "away" if remote else "home"
                c = getattr(race, "commentator", None)
                v["ai"]["commentary"] = bool(c and c.on)
                v["ai"]["topics"] = commentary.TOPICS[commentary.kind(race)]
                if not remote and gate.enabled():
                    v["share"] = {"link": gate.link, "passcode": gate.passcode}
                body = json.dumps(v).encode("utf-8")
                ctype = "application/json"
            elif self.path.startswith("/predictions"):
                body = json.dumps(preds.state()).encode("utf-8")
                ctype = "application/json"
            elif self.path.startswith("/track/"):
                try:
                    _, _, key, year = self.path.split("/")[:4]
                    data = track.outline(int(key), int(year))
                except Exception as e:
                    log.info("Track outline problem: %s", e)
                    data = None
                body = json.dumps(data).encode("utf-8")
                ctype = "application/json"
            elif self.path.startswith("/weekend"):
                try:
                    data = weekend.view()
                except Exception as e:
                    log.info("Weekend view problem: %s", e)
                    data = {"error": "Couldn't put the weekend together (%s)" % e}
                return self._json(data)
            elif self.path.startswith("/history/"):
                q = urllib.parse.urlparse(self.path)
                args = urllib.parse.parse_qs(q.query)
                day = race.session().get("StartDate") or time.strftime("%Y-%m-%d")
                try:
                    if q.path == "/history/race":
                        data = history.race(int((args.get("season") or ["0"])[0]), int((args.get("round") or ["0"])[0]))
                    elif q.path == "/history/team":
                        data = history.team(re.sub(r"[^A-Za-z]", "", (args.get("code") or [""])[0])[:3], day)
                    elif q.path == "/history/driver":
                        data = history.driver(re.sub(r"[^A-Za-z]", "", (args.get("code") or [""])[0])[:3], day)
                    else:
                        data = history.circuit(day)
                except history.TooBusy as e:
                    data = {"error": str(e)}
                except Exception as e:
                    log.info("History lookup problem: %s", e)
                    data = {"error": "Couldn't get that from Jolpica just now (%s)" % e}
                return self._json(data)
            elif self.path.startswith("/radio?p="):
                audio, why = radio.fetch(urllib.parse.unquote(self.path[len("/radio?p="):]))
                if audio is None:
                    return self._json({"error": why})
                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg")
                self.end_headers()
                self.wfile.write(audio)
                return
            elif self.path in ("/", "/index.html"):
                body, ctype = page, "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def main():
    ap = argparse.ArgumentParser(description="Ed's Race Weekend Dashboard")
    ap.add_argument("--replay", nargs=3, metavar=("YEAR", "GRAND_PRIX", "SESSION"),
                    help="replay a past session, e.g. 2026 Singapore Race")
    ap.add_argument("--folder", help="replay from saved .jsonStream files in this folder")
    ap.add_argument("--speed", type=float, default=10, help="replay speed (default 10x)")
    ap.add_argument("--from-lap", type=int, default=0, help="jump the replay to this lap")
    ap.add_argument("--demo", nargs="?", const="race", choices=["race", "quali"],
                    help="play a made-up race (or 'quali' for qualifying) to try the screen out")
    ap.add_argument("--no-record", action="store_true", help="don't save live sessions to the recordings folder")
    ap.add_argument("--test-ai", action="store_true", help="check the AI key in config.py works (Claude or Gemini), then exit")
    ap.add_argument("--test-radio", nargs="?", const="sample", metavar="AUDIO_FILE",
                    help="check radio transcription works, on a sample clip or your own audio file")
    ap.add_argument("--share", nargs="?", const="new", metavar="PASSCODE",
                    help="share a private link for people outside the house (new six-digit passcode unless you give one)")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--check-fia", action="store_true", help="list the stewards' decisions on the FIA's documents page now, then exit")
    args = ap.parse_args()

    if args.test_radio:
        import radio as radio_mod
        if not radio_mod.HAVE_WHISPER:
            raise SystemExit("\nThe speech model isn't installed yet. Run:\n  py -m pip install -r requirements-radio.txt\n")
        if args.test_radio == "sample":
            url = "https://github.com/SYSTRAN/faster-whisper/raw/master/tests/data/jfk.flac"
            print("Fetching a short public domain test clip (JFK, 1961)...")
            audio = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "race-weekend-dashboard/1.0"}), timeout=30).read()
        else:
            audio = open(args.test_radio, "rb").read()
        print("Transcribing. The first time downloads the speech model, so give it a minute...")
        t0 = time.time()
        text = radio_mod.Radio.__new__(radio_mod.Radio)
        text.model = None
        out = radio_mod.Radio._transcribe(text, audio)
        raise SystemExit("\nIt works (%.1f seconds). Heard:\n  \"%s\"\n" % (time.time() - t0, out))

    if args.check_fia:
        w = weekend.view()
        if not w.get("name"):
            raise SystemExit("\nCouldn't work out this weekend's event: %s\n" % w.get("error"))
        try:
            html = fia.Checker._fetch()
        except Exception as e:
            raise SystemExit("\nCouldn't read the FIA's documents page (%s)\n" % e)
        docs = fia.documents(html, w["name"], (w.get("place") or "").split(",")[0])
        print("\n%s: %d documents on the FIA page, %d of them decisions:\n" % (
            w["name"], len(docs), len([1 for u, t in docs if fia.is_decision(t)])))
        for u, t in docs:
            print("  %s %s" % ("*" if fia.is_decision(t) else " ", t))
        print("\n(* = would go in the feed)\n\nWhat the newest three decisions say:")
        for u, t in [d for d in docs if fia.is_decision(d[1])][:3]:
            try:
                fields = fia.parse_decision(fia.pdf_text(fia.Checker._fetch_pdf(u)))
            except Exception as e:
                fields = {}
                print("  (couldn't open it: %s)" % e)
            print("\n  " + t)
            for line in fia.summary(fields) or ["(no decision found in the PDF, or pypdf isn't installed: py -m pip install pypdf)"]:
                print("    " + line)
        raise SystemExit("")

    if args.test_ai:
        from notify import fallback
        lines = []
        for who in [provider()] + ([fallback()] if fallback() else []):
            lines.append(check_ai(who, backup=who != provider()))
        raise SystemExit("\n" + "\n".join(lines) + "\n")

    race = None

    def context(full=False):
        v = race.view()
        rows = v["rows"] if full else v["rows"][:12]
        lines = []
        for r in rows:
            if r["out"]:
                lines.append("OUT %s (%s)" % (r["name"], r["team"]))
                continue
            bits = ["P%d %s (%s)" % (r["pos"], r["name"], r["team"]), "gap %s" % (r["gap"] or "-")]
            if r["interval"]:
                bits.append("interval %s" % r["interval"])
            if r["tyre"]:
                bits.append("%s tyres %s laps old" % (r["tyre"].lower(), r["tyreAge"]))
            if full:
                bits.append("%s stops" % (r["stops"] or 0))
                if r["inPit"]:
                    bits.append("IN PIT")
                if r["watchers"]:
                    bits.append("followed by " + " and ".join(r["watchers"]))
            lines.append(", ".join(bits))
        if v.get("isQuali"):
            q = v["quali"]
            text = "%s %s, Q%s.%s Track: %s." % (v["meeting"], v["session"], q["part"],
                                                 (" The top %s go through." % q["cutAfter"]) if q.get("cutAfter") else " Shoot-out for pole.",
                                                 v["trackText"])
            lines = []
            for r in rows:
                if r.get("knockedOut"):
                    lines.append("P%d %s (%s), knocked out" % (r["pos"], r["name"], r["team"]))
                    continue
                bits = ["P%d %s (%s)" % (r["pos"], r["name"], r["team"]), "best %s" % (r.get("partBest") or "no time yet")]
                if r.get("toCut") is not None and q.get("cutAfter"):
                    bits.append("%+.3f to the cut" % r["toCut"])
                if full and r["watchers"]:
                    bits.append("followed by " + " and ".join(r["watchers"]))
                lines.append(", ".join(bits))
        elif race.is_race():
            text = "%s %s. Lap %s of %s. Track: %s." % (v["meeting"], v["session"], v["lap"], v["total"], v["trackText"])
        else:
            # practice: no race order, so give each car's best lap and how far off the top it is
            text = "%s %s (a practice session, so times matter more than positions). Track: %s." % (
                v["meeting"], v["session"], v["trackText"])
            lines = []
            for r in rows:
                bits = ["P%d %s (%s)" % (r["pos"], r["name"], r["team"]),
                        "best %s" % (r.get("best") or "no time yet")]
                if r["gap"]:
                    bits.append("%s off the top" % r["gap"])
                if r["tyre"]:
                    bits.append("%s tyres %s laps old" % (r["tyre"].lower(), r["tyreAge"]))
                if r["inPit"]:
                    bits.append("in the garage")
                if full and r["watchers"]:
                    bits.append("followed by " + " and ".join(r["watchers"]))
                lines.append(", ".join(bits))
        if v.get("air"):
            text += " Air %s C, track %s C, %s." % (v["air"], v["trackTemp"], "raining" if v.get("rain") not in (None, "0") else "dry")
        text += "\n" + "\n".join(lines)
        if full:
            recent = [e["text"] for e in v["events"][:8]]
            if recent:
                text += "\nRecent updates, newest first:\n" + "\n".join(recent)
        return text

    notifier = Notifier(context)
    race = Race(notifier)
    race.ask_context = lambda: context(full=True)
    radio = Radio(race)
    race.radio_hook = radio
    race.standings = Standings()
    race.commentator = commentary.Commentator(race, lambda t, text: race.say(t, text, auto=True))

    def fia_doc(title, url, info=None):
        text, who = fia.car_names(race, title)
        kind = title.split(" - ")[0].upper()
        info = info or {}
        n = fia.driver_number(info.get("fields") or {})
        with race.lock:
            if n and n in race.drivers():
                who = set(who) | set(race.watchers(n))
            extra = {"link": url}
            if info.get("lines"):
                extra["detail"] = info["lines"]
            race.emit(text, who=tuple(who) if who else EVERYONE, title="FIA DOCUMENT · " + kind,
                      kind="stewards", send=bool(who), extra=extra)
    race.fia = fia.Checker(race, fia_doc)

    def warm_history():
        """Once the driver list has arrived, quietly look up everyone the family follows."""
        for _ in range(60):
            if race.drivers():
                break
            time.sleep(5)
        with race.lock:
            codes = [d.get("Tla") for n, d in race.drivers().items() if d.get("Tla") and race.watchers(n)]
        if not codes:
            codes = sorted({c for p in config.PEOPLE.values() for c in p.get("drivers", [])})
        day = race.session().get("StartDate") or time.strftime("%Y-%m-%d")
        history.warm_up(codes, day)
    if not getattr(args, "no_history", False):
        threading.Thread(target=warm_history, daemon=True).start()
    preds = Predictions(race)

    def after_finish(order):
        if ai_ready() and notifier.enabled:
            def go():
                try:
                    debrief(race, preds)
                except Exception as e:
                    log.warning("Race debrief failed: %s", e)
            threading.Timer(3, go).start()
    race.finish_hooks.append(after_finish)
    log.info("Team radio transcripts: %s", "ready" if radio.available()
             else "off (install with: py -m pip install -r requirements-radio.txt)")

    def status(msg):
        race.status = msg
        log.info(msg)

    if args.demo:
        args.folder = os.path.join(HERE, "demo" if args.demo == "race" else "demo-quali")
        if args.speed == 10:
            args.speed = 20
    if args.replay or args.folder:
        path = None
        if not args.folder:
            year, gp, sess = args.replay
            try:
                path = find_session(year, gp, sess)
            except ArchiveLocked:
                raise SystemExit(
                    "\nF1 locked its archive of past sessions in mid October 2026, so it now needs an\n"
                    "F1 TV login and replays from it won't work. Live timing during sessions is\n"
                    "still open, and the dashboard records every live session to the 'recordings'\n"
                    "folder, so you can replay those with --folder. To see the screen working now,\n"
                    "try:  py dashboard.py --demo\n")
            except OSError as e:
                raise SystemExit("\nCouldn't reach F1's servers (%s). Check the internet connection.\n" % e)
        feed = ReplayFeed(race.update, status, speed=args.speed, path=path,
                          folder=args.folder, start_lap=args.from_lap, clock=race.set_clock)
    else:
        rec = None if args.no_record else Recorder(os.path.join(HERE, "recordings"))
        feed = LiveFeed(race.update, status, recorder=rec)
        status("Connecting to F1 live timing...")

    threading.Thread(target=feed.run, daemon=True).start()
    gate = share.Gate()
    if args.share:
        code = re.sub(r"\D", "", args.share) if args.share != "new" else share.new_passcode()
        if len(code) != 6:
            raise SystemExit("\nThe passcode needs to be six numbers, e.g.  py dashboard.py --share 271024\n")
        gate.passcode = code

        def announce(link):
            log.info("")
            log.info("=" * 64)
            log.info("  SHARING IS ON. Send Dad these two things:")
            log.info("    Link:      %s", link)
            log.info("    Passcode:  %s", code)
            if getattr(config, "SHARE_HOSTNAME", ""):
                log.info("  The link stays the same every time; the passcode changes unless you pick one.")
            else:
                log.info("  Both change each time you start with --share.")
            log.info("=" * 64)
            log.info("")
        share.start_tunnel(config.WEB_PORT, gate, announce)
    try:
        server = ThreadingHTTPServer(("0.0.0.0", config.WEB_PORT), make_handler(race, radio, preds, gate))
    except OSError:
        raise SystemExit("\nPort %d is already in use. Is the dashboard already running in another window?\n"
                         "Close that one first, or change WEB_PORT in config.py.\n" % config.WEB_PORT)
    url = "http://localhost:%d" % config.WEB_PORT
    log.info("Dashboard running at %s (Ctrl+C to stop)", url)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Stopped")


if __name__ == "__main__":
    main()
