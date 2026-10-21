"""Circuit outlines for the map, from MultiViewer's free circuit data (the same
source FastF1 uses). Each one is fetched once and kept in the 'tracks' folder."""
import json
import logging
import math
import os
import urllib.request

log = logging.getLogger("dashboard")
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "tracks")
URL = "https://api.multiviewer.app/api/v1/circuits/%s/%s"
SIZE = 1000.0


def _download(key, year):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, "%s_%s.json" % (key, year))
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    req = urllib.request.Request(URL % (key, year), headers={"User-Agent": "race-weekend-dashboard/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode("utf-8"))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return data


def _nearest(points, x, y):
    return min(range(len(points)), key=lambda i: (points[i][0] - x) ** 2 + (points[i][1] - y) ** 2)


_failed = set()


def outline(key, year):
    """Returns {'points': [[x,y],...] starting near the start line, 'corners': [...]}
    scaled to a 1000 x 1000 box, or None if it can't be had."""
    if (key, year) in _failed:
        return None
    data = None
    for y in (int(year), int(year) - 1):
        try:
            data = _download(key, y)
            if data:
                break
        except Exception as e:
            log.info("No circuit outline for %s/%s (%s)", key, y, e)
    if not data:
        _failed.add((key, year))
        return None

    corners = []
    for c in data.get("corners") or []:
        tp = c.get("trackPosition") or {}
        if "x" in tp and "y" in tp:
            corners.append((int(c.get("number", 0)), str(c.get("letter", "") or ""), float(tp["x"]), float(tp["y"])))
    corners.sort()
    msecs = []
    for c in data.get("marshalSectors") or []:
        tp = c.get("trackPosition") or {}
        if "x" in tp and "y" in tp:
            msecs.append((int(c.get("number", 0)), float(tp["x"]), float(tp["y"])))

    xs, ys = data.get("x") or [], data.get("y") or []
    if len(xs) > 20 and len(xs) == len(ys):
        pts = [(float(a), float(b)) for a, b in zip(xs, ys)]
    elif len(corners) > 4:
        pts = [(c[2], c[3]) for c in corners]       # rough shape from the corners alone
    else:
        return None

    # rotate as MultiViewer suggests, and flip y for the screen
    ang = math.radians(float(data.get("rotation", 0) or 0))
    ca, sa = math.cos(ang), math.sin(ang)
    rot = lambda x, y: (x * ca - y * sa, -(x * sa + y * ca))
    pts = [rot(x, y) for x, y in pts]
    corners = [(n, l, *rot(x, y)) for n, l, x, y in corners]
    msecs = [(n, *rot(x, y)) for n, x, y in msecs]

    # thin out very long traces
    step = max(1, len(pts) // 600)
    pts = pts[::step]

    # start the outline just before turn 1, running towards turn 2
    if len(corners) >= 2:
        i1 = _nearest(pts, corners[0][2], corners[0][3])
        i2 = _nearest(pts, corners[1][2], corners[1][3])
        n = len(pts)
        if (i2 - i1) % n > n / 2:          # turn 2 is "behind" turn 1, so run the other way
            pts.reverse()
            i1 = n - 1 - i1
        back = max(1, n // 40)
        start = (i1 - back) % n
        pts = pts[start:] + pts[:start]

    # where each marshal sector starts along the outline
    sectors = sorted(({"n": n, "i": _nearest(pts, x, y)} for n, x, y in msecs), key=lambda d: d["i"])

    # fit into a square box with a margin
    allx = [p[0] for p in pts]
    ally = [p[1] for p in pts]
    minx, maxx, miny, maxy = min(allx), max(allx), min(ally), max(ally)
    scale = (SIZE * 0.9) / max(maxx - minx, maxy - miny, 1)
    ox = (SIZE - (maxx - minx) * scale) / 2
    oy = (SIZE - (maxy - miny) * scale) / 2
    fit = lambda x, y: [round(ox + (x - minx) * scale, 1), round(oy + (y - miny) * scale, 1)]
    return {
        "name": data.get("circuitName", ""),
        "points": [fit(x, y) for x, y in pts],
        "corners": [{"n": "%d%s" % (n, l), "x": fit(x, y)[0], "y": fit(x, y)[1]} for n, l, x, y in corners],
        "sectors": sectors,
    }
