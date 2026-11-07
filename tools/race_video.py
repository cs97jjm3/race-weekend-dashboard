"""Makes a short video of a race from a recording, to share.

The whole race in about a minute: the cars going round the track map, the top ten, the big
moments as they happen, and the result at the end. It replays the recording through the
same race logic as the dashboard, so it shows exactly what the screen showed.

    py tools\\race_video.py                     the latest recording
    py tools\\race_video.py recordings\\2026-11-08_1655
    py tools\\race_video.py demo --seconds 45

It needs two free packages:  py -m pip install -r requirements-video.txt
The video goes in the 'videos' folder as an .mp4 that WhatsApp, LinkedIn and phones all play.
Family names are left off, so it's safe to post publicly.
"""
import argparse
import glob
import importlib.util
import math
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if not os.path.exists(os.path.join(ROOT, "config.py")):
    spec = importlib.util.spec_from_file_location("config", os.path.join(ROOT, "config.example.py"))
    _config = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(_config)
    sys.modules["config"] = _config

import config                                   # noqa: E402
from feed import REPLAY_TOPICS, _parse_stream   # noqa: E402
from race import Race                           # noqa: E402
import track                                    # noqa: E402

W, H, FPS = 1280, 720, 25
BG, PANEL, LINE, INK, DIM = (11, 13, 16), (17, 21, 26), (34, 41, 50), (233, 237, 242), (136, 147, 160)
ACCENT, YELLOW, RED = (200, 240, 49), (245, 197, 66), (232, 0, 45)
BRAND = "Ed's Race Weekend Dashboard  ·  edsf1.jmmcp.uk"
TICKER_KINDS = {"start", "sc", "red", "green", "lead", "pit", "out", "fast", "finish", "up", "stewards"}


class Quiet:
    enabled = False

    def __call__(self, *a, **k):
        pass


# ---------- drawing helpers ----------

def font(size, bold=False):
    from PIL import ImageFont
    names = (["arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"] if bold else
             ["arial.ttf", "Arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"])
    dirs = ["C:\\Windows\\Fonts", "/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/truetype/liberation",
            "/Library/Fonts", "/System/Library/Fonts/Supplemental"]
    for n in names:
        for d in dirs:
            p = os.path.join(d, n)
            if os.path.exists(p):
                return ImageFont.truetype(p, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def hexrgb(h, fallback=(136, 136, 136)):
    try:
        h = str(h).lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, IndexError):
        return fallback


class TrackPath:
    """The circuit outline fitted into a box, with a way to find the point a fraction round it."""

    def __init__(self, points, box):
        x0, y0, x1, y1 = box
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        sx = (x1 - x0) / max(1, max(xs) - min(xs))
        sy = (y1 - y0) / max(1, max(ys) - min(ys))
        s = min(sx, sy)
        ox = x0 + ((x1 - x0) - (max(xs) - min(xs)) * s) / 2
        oy = y0 + ((y1 - y0) - (max(ys) - min(ys)) * s) / 2
        self.pts = [(ox + (x - min(xs)) * s, oy + (y - min(ys)) * s) for x, y in points]
        self.cum = [0.0]
        for a, b in zip(self.pts, self.pts[1:] + self.pts[:1]):
            self.cum.append(self.cum[-1] + math.dist(a, b))
        self.total = self.cum[-1] or 1

    def at(self, frac):
        d = (frac % 1) * self.total
        lo, hi = 0, len(self.cum) - 1
        while lo < hi - 1:
            mid = (lo + hi) // 2
            if self.cum[mid] <= d:
                lo = mid
            else:
                hi = mid
        a, b = self.pts[lo % len(self.pts)], self.pts[(lo + 1) % len(self.pts)]
        seg = (self.cum[lo + 1] - self.cum[lo]) or 1
        t = (d - self.cum[lo]) / seg
        return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t


def oval(n=240):
    return [[500 + 420 * math.cos(math.pi / 2 + i / n * math.tau), 500 + 300 * math.sin(math.pi / 2 + i / n * math.tau)]
            for i in range(n)]


# ---------- the replay ----------

def load(folder):
    entries = []
    for t in REPLAY_TOPICS:
        f = os.path.join(folder, t + ".jsonStream")
        if os.path.exists(f):
            with open(f, encoding="utf-8-sig") as fh:
                entries += _parse_stream(fh.read(), t)
    entries.sort(key=lambda e: e[0])
    if not entries:
        raise SystemExit("No recording found in %s" % folder)
    return entries


def race_window(entries):
    """From just before lights out to just after the chequered flag, in recording time."""
    race, start, end = Race(Quiet()), None, None
    for t, topic, data in entries:
        race.set_clock(t, 1)
        before = len(race.events)
        race.update(topic, data)
        for e in race.events[:len(race.events) - before]:
            if e["kind"] == "start" and start is None and race.is_race():
                start = t
            if e["kind"] == "finish" and end is None:
                end = t
    if not race.is_race():
        raise SystemExit("That recording isn't a race or sprint, so there's nothing to race through.")
    return (start or entries[0][0]) - 8, (end or entries[-1][0]) + 6


def frames(entries, seconds, outline_for):
    t0, t1 = race_window(entries)
    n = int(seconds * FPS)
    race = Race(Quiet())
    i, seen = 0, 0
    path, ticker, finished = None, [], None
    for k in range(n):
        t = t0 + (t1 - t0) * k / max(1, n - 1)
        while i < len(entries) and entries[i][0] <= t:
            race.set_clock(entries[i][0], 1)
            race.update(entries[i][1], entries[i][2])
            i += 1
        race.set_clock(t, 1)
        v = race.view()
        if path is None and v.get("circuit"):
            path = outline_for(v)
        fresh = len(race.events) - seen
        for e in reversed(race.events[:fresh]):
            if e["kind"] in TICKER_KINDS and (e.get("say") or (e["kind"] == "up" and e["who"])):
                ticker.append((k, e.get("say") or e["text"], e["kind"]))
            if e["kind"] == "finish":
                finished = finished or k
        seen = len(race.events)
        yield k, n, v, path, ticker, finished


def draw_frame(v, path, ticker, k, finished, fonts, track_box):  # noqa: ARG001
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    rows = sorted([r for r in v["rows"] if r["pos"] < 99 and not r.get("out")], key=lambda r: r["pos"])

    # header
    d.text((40, 26), (v.get("meeting") or "Race").upper(), font=fonts["h"], fill=ACCENT)
    lap, total = v.get("lap"), v.get("total")
    d.text((40, 62), ("LAP %s / %s" % (lap, total)) if lap else "LIGHTS OUT", font=fonts["lap"], fill=INK)
    flag = v.get("track")
    if flag in ("SC", "VSC", "VSC_END", "YELLOW", "RED"):
        colour = RED if flag == "RED" else YELLOW
        label = {"SC": "SAFETY CAR", "VSC": "VIRTUAL SAFETY CAR", "VSC_END": "VSC ENDING",
                 "YELLOW": "YELLOW FLAG", "RED": "RED FLAG"}[flag]
        d.rounded_rectangle((520, 30, 720, 72), 8, fill=colour)
        d.text((620, 51), label, font=fonts["b"], fill=(15, 15, 15), anchor="mm")

    # track and cars
    if path:
        d.line(path.pts + path.pts[:1], fill=(40, 47, 56), width=16, joint="curve")
        d.line(path.pts + path.pts[:1], fill=(70, 80, 92), width=4, joint="curve")
        sx, sy = path.at(0)
        d.ellipse((sx - 6, sy - 6, sx + 6, sy + 6), fill=INK)
        mine = []
        for r in reversed(rows):
            if r.get("progress") is None or r.get("out"):
                continue
            x, y = path.at(r["progress"])
            col = hexrgb(r.get("colour"))
            if r["watchers"]:
                mine.append((x, y, col, r))
                continue
            d.ellipse((x - 8, y - 8, x + 8, y + 8), fill=col, outline=BG, width=2)
        for x, y, col, r in mine:
            d.ellipse((x - 12, y - 12, x + 12, y + 12), fill=col, outline=INK, width=3)
            d.text((x + 15, y - 12), r["tla"], font=fonts["b"], fill=INK, stroke_width=3, stroke_fill=BG)

    # top ten
    x0 = 790
    d.rounded_rectangle((x0 - 20, 24, W - 30, 470), 12, fill=PANEL)
    for j, r in enumerate(rows[:10]):
        y = 40 + j * 42
        col = hexrgb(r.get("colour"))
        d.rectangle((x0, y + 6, x0 + 6, y + 30), fill=col)
        d.text((x0 + 18, y + 18), str(r["pos"]), font=fonts["b"], fill=DIM, anchor="lm")
        d.text((x0 + 58, y + 18), r["tla"], font=fonts["row"], fill=INK if r["watchers"] else (205, 210, 216), anchor="lm")
        gap = "Leader" if r["pos"] == 1 else (r.get("gap") or "")
        if r.get("inPit"):
            gap = "PIT"
        d.text((W - 54, y + 18), gap, font=fonts["row"], fill=DIM, anchor="rm")
        if r["watchers"]:
            d.ellipse((x0 + 128, y + 13, x0 + 138, y + 23), fill=ACCENT)

    # ticker: the latest big moment, for about two seconds
    recent = [x for x in ticker if k - x[0] < FPS * 2.4]
    if recent:
        _, text, kind = recent[-1]
        col = RED if kind == "red" else YELLOW if kind == "sc" else ACCENT
        d.rounded_rectangle((x0 - 20, 490, W - 30, 600), 12, fill=PANEL, outline=col, width=3)
        lines = wrap(text, fonts["tick"], W - 30 - x0 - 10, d)
        for j, line in enumerate(lines[:3]):
            d.text((x0, 506 + j * 30), line, font=fonts["tick"], fill=INK)

    d.text((W // 2, H - 26), BRAND, font=fonts["small"], fill=DIM, anchor="mm")

    return img


def draw_result(base, v, a, fonts):
    """The result card over the last frame, faded in by `a` (0 to 1)."""
    from PIL import Image, ImageDraw
    rows = sorted([r for r in v["rows"] if r["pos"] < 99 and not r.get("out")], key=lambda r: r["pos"])
    over = Image.new("RGBA", (W, H), (11, 13, 16, int(235 * a)))
    img = Image.alpha_composite(base.convert("RGBA"), over).convert("RGB")
    if a < 0.5:
        return img
    d = ImageDraw.Draw(img)
    d.text((W // 2, 110), "CHEQUERED FLAG", font=fonts["h"], fill=ACCENT, anchor="mm")
    d.text((W // 2, 150), (v.get("meeting") or "").upper(), font=fonts["small"], fill=DIM, anchor="mm")
    for j, r in enumerate(rows[:3]):
        y = 240 + j * 92
        col = hexrgb(r.get("colour"))
        d.rounded_rectangle((W // 2 - 300, y - 36, W // 2 + 300, y + 36), 12, fill=PANEL)
        d.rectangle((W // 2 - 300, y - 36, W // 2 - 290, y + 36), fill=col)
        d.text((W // 2 - 260, y), "P%d" % r["pos"], font=fonts["lap"], fill=DIM, anchor="lm")
        d.text((W // 2 - 160, y), r["name"], font=fonts["lap"], fill=INK, anchor="lm")
        d.text((W // 2 + 280, y), r.get("team", ""), font=fonts["small"], fill=DIM, anchor="rm")
    ours = [r for r in rows[3:] if r["watchers"]]
    if ours:
        d.text((W // 2, 540), "   ".join("P%d %s" % (r["pos"], r["name"]) for r in ours[:5]),
               font=fonts["tick"], fill=INK, anchor="mm")
    d.text((W // 2, H - 26), BRAND, font=fonts["small"], fill=DIM, anchor="mm")
    return img


def draw_title(v, fonts, a):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    c = tuple(int(BG[i] + (x - BG[i]) * a) for i, x in enumerate(ACCENT))
    d.text((W // 2, H // 2 - 40), (v.get("meeting") or "The race").upper(), font=fonts["big"], fill=c, anchor="mm")
    d.text((W // 2, H // 2 + 20), "The %s in a minute" % (v.get("session") or "race").lower(),
           font=fonts["tick"], fill=tuple(int(BG[i] + (x - BG[i]) * a) for i, x in enumerate(INK)), anchor="mm")
    d.text((W // 2, H - 26), BRAND, font=fonts["small"], fill=DIM, anchor="mm")
    return img


def wrap(text, f, width, d):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if d.textlength(trial, font=f) <= width:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def latest_recording():
    folders = sorted(glob.glob(os.path.join(ROOT, "recordings", "*")), key=os.path.getmtime, reverse=True)
    for f in folders:
        if os.path.exists(os.path.join(f, "TimingData.jsonStream")):
            return f
    raise SystemExit("No recordings yet. Try:  py tools\\race_video.py demo")


def make(folder, seconds=60, out=None):
    try:
        from PIL import Image  # noqa: F401
        import imageio_ffmpeg
    except ImportError:
        raise SystemExit("\nThis needs two free packages first:\n  py -m pip install -r requirements-video.txt\n")
    entries = load(folder)
    fonts = {"big": font(54, True), "h": font(30, True), "lap": font(34, True), "b": font(18, True), "row": font(24, True),
             "tick": font(22), "small": font(16)}
    track_box = (50, 120, 740, 690)

    def outline_for(v):
        year = int(v.get("year") or time.strftime("%Y"))
        o = None
        try:
            o = track.outline(v["circuit"], year)
        except Exception:
            pass
        return TrackPath((o or {}).get("points") or oval(), track_box)

    os.makedirs(os.path.join(ROOT, "videos"), exist_ok=True)
    name = os.path.basename(os.path.normpath(folder))
    out = out or os.path.join(ROOT, "videos", "race-%s.mp4" % name)
    writer = imageio_ffmpeg.write_frames(out, (W, H), fps=FPS, codec="libx264", pix_fmt_out="yuv420p",
                                         quality=7, macro_block_size=8, ffmpeg_log_level="error")
    writer.send(None)
    last = last_v = None
    started = time.time()
    for k, n, v, path, ticker, finished in frames(entries, seconds, outline_for):
        if k == 0:
            for j in range(int(FPS * 1.6)):          # title card
                writer.send(draw_title(v, fonts, min(1.0, j / (FPS * 0.5))).tobytes())
        img = draw_frame(v, path, ticker, k, finished, fonts, track_box)
        writer.send(img.tobytes())
        last, last_v = img, v
        if k % (FPS * 5) == 0:
            print("  %d%%" % (100 * k // n), flush=True)
    for j in range(int(FPS * 4.5)):                  # the result, faded in and held
        writer.send(draw_result(last, last_v, min(1.0, j / (FPS * 0.8)), fonts).tobytes())
    writer.close()
    print("Done in %ds: %s" % (time.time() - started, out))
    return out


def main():
    ap = argparse.ArgumentParser(description="Make a one-minute video of a race from a recording.")
    ap.add_argument("folder", nargs="?", help="a recordings folder, or 'demo' (default: the latest recording)")
    ap.add_argument("--seconds", type=float, default=60, help="how long the race part should last (default 60)")
    a = ap.parse_args()
    folder = a.folder or latest_recording()
    if not os.path.isabs(folder):
        folder = os.path.join(ROOT, folder)
    make(folder, a.seconds)


if __name__ == "__main__":
    main()
