"""Sends alerts to phones through the free ntfy app, with an optional AI comment."""
import json
import logging
import queue
import threading
import time
import urllib.error
import urllib.request

import config

log = logging.getLogger("dashboard")
TAGS = {"sc": "rotating_light", "red": "red_circle", "pit": "wrench", "lead": "trophy",
        "fast": "stopwatch", "battle": "crossed_swords", "up": "arrow_up", "down": "arrow_down",
        "out": "x", "finish": "checkered_flag", "start": "green_circle", "green": "green_circle",
        "rc": "warning"}


def _norm(who):
    who = (who or "").strip().lower()
    return who if who in ("claude", "gemini", "openai") else ""


def provider():
    """Which AI is switched on in config.py: "claude" (the default), "gemini" or "openai"
    (anything that speaks the OpenAI style, e.g. a local FreeLLMAPI)."""
    return _norm(getattr(config, "AI_PROVIDER", "")) or "claude"


def fallback():
    """The AI to try if the first one fails (AI_FALLBACK in config.py), if it's set up."""
    who = _norm(getattr(config, "AI_FALLBACK", ""))
    return who if who and who != provider() and _ready(who) else None


def _key(who=None):
    who = who or provider()
    return {"gemini": getattr(config, "GEMINI_API_KEY", ""),
            "openai": getattr(config, "OPENAI_API_KEY", "")}.get(who, getattr(config, "ANTHROPIC_API_KEY", "")).strip()


def _ready(who):
    if who == "openai":
        return bool(getattr(config, "OPENAI_BASE_URL", "").strip())
    return bool(_key(who))


def ai_ready():
    """True if the chosen AI (or its back-up) has a key."""
    return _ready(provider()) or fallback() is not None


def ai_name(who=None):
    return {"gemini": "Gemini", "openai": "the AI"}.get(who or provider(), "Claude")


def ai_model(who=None):
    who = who or provider()
    return {"gemini": getattr(config, "GEMINI_MODEL", "") or "gemini-3.8-flash",
            "openai": getattr(config, "OPENAI_MODEL", "") or "auto"}.get(who, config.AI_MODEL)


last_used = [None]     # which AI answered last, so the screen can say when the back-up stepped in


class Busy(RuntimeError):
    """The AI service is overloaded or rate-limiting us; the key itself is fine."""


def _post(url, body, headers, timeout, tries=2):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=dict(headers, **{"content-type": "application/json"}))
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read()).get("error", {})
                reason = err.get("message", "") if isinstance(err, dict) else str(err)
            except Exception:
                reason = ""
            if e.code in (429, 500, 502, 503, 529):
                if attempt + 1 < tries:
                    time.sleep(3)          # busy for a moment: one more go
                    continue
                raise Busy("HTTP %s %s" % (e.code, reason or e.reason))
            raise RuntimeError("HTTP %s %s" % (e.code, reason or e.reason))


def _ask(who, prompt, max_tokens, timeout):
    model, key = ai_model(who), _key(who)
    if who == "gemini":
        out = _post("https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent" % model,
                    {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                     "generationConfig": {"maxOutputTokens": max_tokens}},
                    {"x-goog-api-key": key}, timeout)
        cand = (out.get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", [])
                       if not p.get("thought")).strip()
        why = cand.get("finishReason") or (out.get("promptFeedback") or {}).get("blockReason")
    elif who == "openai":
        base = getattr(config, "OPENAI_BASE_URL", "").strip().rstrip("/")
        out = _post(base + "/chat/completions",
                    {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]},
                    {"authorization": "Bearer " + key} if key else {}, timeout)
        choice = (out.get("choices") or [{}])[0]
        text = ((choice.get("message") or {}).get("content") or "").strip()
        why = choice.get("finish_reason")
    else:
        out = _post("https://api.anthropic.com/v1/messages",
                    {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]},
                    {"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout)
        text = "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text").strip()
        why = out.get("stop_reason")
    if not text:
        raise RuntimeError("%s sent back an empty answer (stop reason: %s)" % (ai_name(who), why))
    return text


def ask_claude(prompt, max_tokens=1000, timeout=30, use_fallback=True):
    """One question to the chosen AI, one short answer back. If it fails and AI_FALLBACK is set,
    the back-up AI is asked instead. Raises with a readable reason if both fail.
    (Still called ask_claude because Claude was the first, and is the default.)"""
    first = provider()
    tries = [first] if _ready(first) else []
    if use_fallback and fallback():
        tries.append(fallback())
    if not tries:
        raise RuntimeError("No AI key in config.py")
    errors = []
    for who in tries:
        try:
            text = _ask(who, prompt, max_tokens, timeout)
            last_used[0] = who
            if errors:
                log.info("%s didn't answer (%s), so %s did", ai_name(first), errors[0], ai_name(who))
            return text
        except Exception as e:
            errors.append(e)
    if len(errors) == 1:
        raise errors[0]
    raise RuntimeError("; ".join("%s: %s" % (ai_name(w), e) for w, e in zip(tries, errors)))


def used_backup():
    """True if the last answer came from the back-up AI rather than the usual one."""
    return last_used[0] is not None and last_used[0] != provider()


class Notifier:
    def __init__(self, race_context=None):
        self.q = queue.Queue()
        self.context = race_context    # function returning a short text summary of the race
        self.enabled = ai_ready()   # the screen's AI switch
        threading.Thread(target=self._worker, daemon=True).start()

    def __call__(self, event, loud=False):
        self.q.put((event, loud))

    def _ai(self, event):
        if not self.enabled or not ai_ready() or event["kind"] not in ("pit", "finish", "sc", "red"):
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
