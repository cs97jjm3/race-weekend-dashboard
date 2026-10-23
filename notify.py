"""Sends alerts to phones through the free ntfy app, with an optional AI comment."""
import json
import logging
import queue
import threading
import urllib.error
import urllib.request

import config

log = logging.getLogger("dashboard")
TAGS = {"sc": "rotating_light", "red": "red_circle", "pit": "wrench", "lead": "trophy",
        "fast": "stopwatch", "battle": "crossed_swords", "up": "arrow_up", "down": "arrow_down",
        "out": "x", "finish": "checkered_flag", "start": "green_circle", "green": "green_circle",
        "rc": "warning"}


def ask_claude(prompt, max_tokens=80, timeout=30):
    """One question to Claude, one short answer back. Raises with a readable reason if it fails."""
    body = json.dumps({"model": config.AI_MODEL, "max_tokens": max_tokens,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers={
        "x-api-key": config.ANTHROPIC_API_KEY.strip(), "anthropic-version": "2023-06-01",
        "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            reason = json.loads(e.read()).get("error", {}).get("message", "")
        except Exception:
            reason = ""
        raise RuntimeError("HTTP %s %s" % (e.code, reason or e.reason))
    text = "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text").strip()
    if not text:
        raise RuntimeError("Claude sent back an empty answer (stop reason: %s)" % out.get("stop_reason"))
    return text


class Notifier:
    def __init__(self, race_context=None):
        self.q = queue.Queue()
        self.context = race_context    # function returning a short text summary of the race
        self.enabled = bool(config.ANTHROPIC_API_KEY.strip())   # the screen's AI switch
        threading.Thread(target=self._worker, daemon=True).start()

    def __call__(self, event, loud=False):
        self.q.put((event, loud))

    def _ai(self, event):
        if not self.enabled or not config.ANTHROPIC_API_KEY or event["kind"] not in ("pit", "finish", "sc", "red"):
            return ""
        if event.get("shown") is False:
            return ""      # not on the screen, so don't spend on it
        prompt = ("You're a calm, plain-spoken F1 race engineer talking to a fan. In one short sentence "
                  "(under 25 words, no hype, no emojis), add something useful to this alert that it doesn't "
                  "already say. Only use facts from the race data below.\n\n"
                  "Alert: %s\n\nRace so far:\n%s" % (event["text"], self.context() if self.context else ""))
        try:
            return ask_claude(prompt)
        except Exception as e:
            log.warning("AI comment skipped: %s", e)
            return ""

    def _worker(self):
        while True:
            event, loud = self.q.get()
            extra = self._ai(event)
            if extra:
                event["ai"] = extra
            if event.get("ntfy") is False:
                continue
            for person in event["who"]:
                topic = (config.PEOPLE.get(person) or {}).get("ntfy_topic")
                if not topic:
                    continue
                msg = event["text"] + ("\n" + extra if extra else "")
                req = urllib.request.Request("https://ntfy.sh/" + topic, data=msg.encode("utf-8"), headers={
                    "Title": event["title"].encode("ascii", "ignore").decode(),
                    "Tags": TAGS.get(event["kind"], "racing_car"),
                    "Priority": "high" if loud else "default"})
                try:
                    urllib.request.urlopen(req, timeout=timeout).read()
                except Exception as e:
                    log.warning("Couldn't send alert to %s: %s", person, e)
