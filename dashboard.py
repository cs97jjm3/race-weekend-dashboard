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
from notify import Notifier
from predictions import Predictions
from radio import Radio
from race import Race
from standings import Standings
import share
import history

HERE = os.path.dirname(os.path.abspath(__file__))
logging.basicConfig(format="%(asctime)s %(message)s", level=logging.INFO)
log = logging.getLogger("dashboard")


ASK_GAP_SECONDS = 20
_last_ask = [0.0]


def who_follows(race):
    by_code = {d.get("Tla"): race.name(n) for n, d in race.drivers().items()}
    return "; ".join("%s follows %s" % (name, ", ".join(p["teams"] + [by_code.get(c, c) for c in p["drivers"]]))
                     for name, p in config.PEOPLE.items())


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
        race.emit(text, title="RACE DEBRIEF", kind="debrief", send=False)
    return text


def make_handler(race, radio, preds, gate):
    def failed(e):
        """Claude didn't answer: say so in the cmd window and in the feed, not just on the button."""
        log.warning("'What's happening?' failed: %s", e)
        with race.lock:
            race.emit("Claude didn't answer this time (%s). Try again in a minute." % e,
                      title="AI", kind="info", send=False)
        return {"error": "Claude didn't answer"}

    def ask_now():
        """The 'What's happening?' button: a short read of the race from Claude.
        After the chequered flag it writes the race debrief instead."""
        if not config.ANTHROPIC_API_KEY.strip():
            return {"error": "No Claude key in config.py"}
        if not race.notify.enabled:
            return {"error": "AI is switched off"}
        wait = ASK_GAP_SECONDS - (time.time() - _last_ask[0])
        if wait > 0:
            return {"error": "Give it %d more seconds" % wait}
        _last_ask[0] = time.time()
        if not race.lines():
            return {"error": "No race data yet"}
        if race.finished_sent and race.is_race():
            try:
                debrief(race, preds)
                return {"ok": True}
            except Exception as e:
                return {"error": "Claude didn't answer: %s" % e}
        people = who_follows(race)
        if race.is_quali():
            prompt = ("You're a calm, plain-spoken F1 race engineer on the pit wall with two fans. %s. "
                      "Using ONLY the timing data below, give a quick read of qualifying right now in 2 or 3 short "
                      "sentences: who's quickest, how their drivers stand against the cut, and who's in danger. "
                      "No hype, no emojis, no bullet points, don't invent facts that aren't in the data.\n\n%s"
                      % (people, race.ask_context()))
            try:
                from notify import ask_claude
                reply = ask_claude(prompt, max_tokens=1500)
            except Exception as e:
                return failed(e)
            with race.lock:
                race.emit(reply, title="QUALI READ", kind="ai", send=False)
            return {"ok": True}
        prompt = ("You're a calm, plain-spoken F1 race engineer on the pit wall with two fans. %s. "
                  "Using ONLY the timing data below, give a quick read of the session right now in 2 or 3 short "
                  "sentences: what's happening at the front, anything notable for their drivers, and one thing "
                  "to watch in the next few laps. No hype, no emojis, no bullet points, don't invent facts "
                  "that aren't in the data.\n\n%s" % (people, race.ask_context()))
        try:
            from notify import ask_claude
            reply = ask_claude(prompt, max_tokens=1500)
        except Exception as e:
            return failed(e)
        with race.lock:
            race.emit(reply, title="RACE READ" if race.is_race() else "SESSION READ", kind="ai", send=False)
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
                if config.ANTHROPIC_API_KEY.strip():
                    race.notify.enabled = not race.notify.enabled
                    log.info("AI comments %s", "on" if race.notify.enabled else "off")
                return self._json({"on": race.notify.enabled})
            if self.path == "/ai/ask":
                return self._json(ask_now())
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
            if not self._let_in():
                return
            if self.path.startswith("/state"):
                v = race.view()
                remote = gate.is_remote(self)
                v["viewer"] = "away" if remote else "home"
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
            elif self.path.startswith("/history/"):
                q = urllib.parse.urlparse(self.path)
                args = urllib.parse.parse_qs(q.query)
                day = race.session().get("StartDate") or time.strftime("%Y-%m-%d")
                try:
                    if q.path == "/history/race":
                        data = history.race(int((args.get("season") or ["0"])[0]), int((args.get("round") or ["0"])[0]))
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
    ap.add_argument("--test-ai", action="store_true", help="check the Claude API key works, then exit")
    ap.add_argument("--test-radio", nargs="?", const="sample", metavar="AUDIO_FILE",
                    help="check radio transcription works, on a sample clip or your own audio file")
    ap.add_argument("--share", nargs="?", const="new", metavar="PASSCODE",
                    help="share a private link for people outside the house (new six-digit passcode unless you give one)")
    ap.add_argument("--no-browser", action="store_true")
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

    if args.test_ai:
        from notify import ask_claude
        if not config.ANTHROPIC_API_KEY:
            raise SystemExit("\nNo key yet. Put it between the quotes after ANTHROPIC_API_KEY in config.py.\n")
        try:
            reply = ask_claude("In one short sentence, say hello to James and Ed and wish them a good race weekend.")
        except Exception as e:
            k = config.ANTHROPIC_API_KEY
            checks = []
            checks.append("Length: %d characters (a real key is usually a little over 100)" % len(k.strip()))
            checks.append("Starts with: %s (should be sk-ant-api)" % k.strip()[:10])
            checks.append("Ends with: ...%s (compare with the key's ending shown in the Console)" % k.strip()[-4:])
            if k != k.strip():
                checks.append("Has spaces or line breaks at the start or end (now ignored, but worth tidying)")
            if " " in k.strip():
                checks.append("Has a space in the middle: the paste has gone wrong somewhere")
            if "..." in k or "your key" in k.lower():
                checks.append("Still has the example text in it")
            raise SystemExit("\nThe key didn't work: %s\n\nWhat I can see of the key in config.py:\n  %s\n\n"
                             "401 means Anthropic doesn't recognise it. If it looks right above, make a fresh key in\n"
                             "the Console and paste it again. A message about credit means the account needs topping up.\n"
                             % (e, "\n  ".join(checks)))
        raise SystemExit("\nThe key works. Claude says: %s\n" % reply)

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
    preds = Predictions(race)

    def after_finish(order):
        if config.ANTHROPIC_API_KEY.strip() and notifier.enabled:
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
