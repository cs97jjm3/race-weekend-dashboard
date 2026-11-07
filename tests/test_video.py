"""The race video's track drawing (the video itself needs Pillow and ffmpeg, so isn't made here)."""
import importlib.util
import os

spec = importlib.util.spec_from_file_location(
    "race_video", os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools", "race_video.py"))
race_video = importlib.util.module_from_spec(spec)
spec.loader.exec_module(race_video)


def test_points_round_the_track():
    path = race_video.TrackPath([[0, 0], [100, 0], [100, 100], [0, 100]], (0, 0, 200, 200))
    assert path.at(0) == path.pts[0]
    x, y = path.at(0.125)                     # halfway along the first side
    assert abs(x - 100) < 1 and abs(y - 0) < 1
    assert path.at(1.0) == path.at(0.0)


def test_race_window_finds_lights_out_and_the_flag():
    entries = race_video.load(os.path.join(race_video.ROOT, "demo"))
    t0, t1 = race_video.race_window(entries)
    assert entries[0][0] <= t0 + 8 and t1 - 6 <= entries[-1][0] and t1 > t0
