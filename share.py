"""Letting people outside the house watch, safely.

`py dashboard.py --share` starts a Cloudflare "quick tunnel": a free, encrypted
https:// link to the dashboard, with nothing opened on the router. The link is
new every time, and so is the six-digit passcode, so each race gets a fresh pair.

Anyone at home on the Wi-Fi gets straight in as before. Anyone coming in from
outside sees a passcode page first.
"""
import hashlib
import hmac
import ipaddress
import logging
import re
import secrets
import shutil
import subprocess
import threading
import time

log = logging.getLogger("dashboard")
COOKIE = "rwd_pass"


class Gate:
    def __init__(self, passcode=None):
        self.passcode = passcode or ""
        self.salt = secrets.token_bytes(16)       # new every run, so old logins stop working
        self.fails = {}                           # ip -> [times of wrong guesses]
        self.link = None

    def enabled(self):
        return bool(self.passcode)

    def _token(self):
        return hmac.new(self.salt, self.passcode.encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def is_remote(handler):
        """Coming through the tunnel (Cloudflare adds this header), or from outside the home network."""
        if handler.headers.get("Cf-Connecting-Ip") or handler.headers.get("Cf-Ray"):
            return True
        try:
            ip = ipaddress.ip_address(handler.client_address[0])
            return not (ip.is_private or ip.is_loopback or ip.is_link_local)
        except ValueError:
            return True

    def who(self, handler):
        return handler.headers.get("Cf-Connecting-Ip") or handler.client_address[0]

    def allowed(self, handler):
        if not self.enabled() or not self.is_remote(handler):
            return True
        cookies = handler.headers.get("Cookie") or ""
        m = re.search(r"(?:^|;\s*)%s=([0-9a-f]{64})" % COOKIE, cookies)
        return bool(m) and hmac.compare_digest(m.group(1), self._token())

    def locked_out(self, handler):
        now = time.time()
        recent = [t for t in self.fails.get(self.who(handler), []) if now - t < 600]
        self.fails[self.who(handler)] = recent
        return len(recent) >= 5

    def check(self, handler, guess):
        """Returns a cookie header value on success, otherwise None."""
        if self.locked_out(handler):
            return None
        guess = re.sub(r"\D", "", guess or "")
        if guess and hmac.compare_digest(guess, self.passcode):
            return "%s=%s; Max-Age=259200; Path=/; HttpOnly; Secure; SameSite=Lax" % (COOKIE, self._token())
        self.fails.setdefault(self.who(handler), []).append(time.time())
        log.info("Wrong passcode from %s", self.who(handler))
        return None


def new_passcode():
    return "%06d" % secrets.randbelow(1000000)


def start_tunnel(port, gate, on_link):
    """Run cloudflared and pick the public link out of what it prints."""
    exe = shutil.which("cloudflared")
    if not exe:
        log.warning("")
        log.warning("Sharing needs Cloudflare's free tunnel program. Install it once with:")
        log.warning("    winget install --id Cloudflare.cloudflared")
        log.warning("then close PowerShell, open it again, and run  py dashboard.py --share  again.")
        log.warning("The dashboard is still running for everyone at home.")
        return

    def run():
        while True:
            try:
                p = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", "http://localhost:%d" % port],
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                     encoding="utf-8", errors="replace")
                for line in p.stdout:
                    m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
                    if m and m.group(0) != gate.link:
                        gate.link = m.group(0)
                        on_link(gate.link)
                p.wait()
                log.warning("The sharing tunnel stopped; starting a new one (the link will change).")
            except Exception as e:
                log.warning("Sharing tunnel problem: %s", e)
            gate.link = None
            time.sleep(10)

    threading.Thread(target=run, daemon=True).start()


LOGIN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ed's Race Weekend Dashboard</title>
<style>
  body { margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
         background: #0b0d10; color: #e9edf2; font-family: system-ui, sans-serif; }
  form { width: min(420px, 90vw); text-align: center; }
  h1 { color: #c8f031; font-size: 22px; letter-spacing: 2px; text-transform: uppercase; margin: 0 0 8px; }
  p { color: #8893a0; font-size: 18px; margin: 0 0 22px; }
  input { width: 100%; box-sizing: border-box; font-size: 40px; letter-spacing: 12px; text-align: center;
          padding: 12px; border-radius: 10px; border: 2px solid #3a4552; background: #11151a; color: #e9edf2; }
  input:focus { outline: none; border-color: #c8f031; }
  button { margin-top: 16px; width: 100%; font-size: 22px; font-weight: 700; padding: 14px; border: 0;
           border-radius: 10px; background: #c8f031; color: #0b0d10; cursor: pointer; }
  .err { color: #ff6b81; margin-top: 14px; font-size: 18px; min-height: 22px; }
</style></head><body>
<form method="post" action="/login">
  <h1>Ed's Race Weekend Dashboard</h1>
  <p>Type in the six-number code James sent you.</p>
  <input name="code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" autofocus aria-label="Passcode">
  <button type="submit">Let me in</button>
  <div class="err">{ERR}</div>
</form></body></html>"""


def login_page(message=""):
    import html
    return LOGIN_PAGE.replace("{ERR}", html.escape(message))
