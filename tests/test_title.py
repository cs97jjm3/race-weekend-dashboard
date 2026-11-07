"""Who can still win the title."""
import standings


class FakeRace:
    def __init__(self, name="Race", sprint=False, date="2026-11-08"):
        self.name, self.sprint, self.date = name, sprint, date
    def session(self):
        return {"Name": self.name, "StartDate": self.date + "T17:00:00"}
    def is_sprint(self):
        return self.sprint
    def is_race(self):
        return self.name in ("Race", "Sprint")


def _st(round_now=20):
    st = standings.Standings()
    st.data = {"round": round_now, "drivers": [], "teams": [],
               "races": [{"round": 21, "date": "2026-11-08", "sprint": "2026-11-07"},
                         {"round": 22, "date": "2026-11-21", "sprint": ""},
                         {"round": 23, "date": "2026-12-06", "sprint": ""}]}
    return st


def _d(code, before, gain):
    return {"code": code, "name": code.title(), "before": before, "after": before + gain, "watchers": []}


def test_points_left_during_the_race():
    pts, races, sprints = _st().points_left(FakeRace("Race"), True)
    assert (races, sprints) == (2, 0) and pts == 50


def test_points_left_during_the_sprint_includes_sunday():
    pts, races, sprints = _st().points_left(FakeRace("Sprint", sprint=True, date="2026-11-07"), True)
    assert (races, sprints) == (3, 0) and pts == 75


def test_knocked_out_and_champion_if_it_finished_now():
    st = _st()
    drivers = [_d("NOR", 400, 25), _d("PIA", 360, 0), _d("VER", 330, 0)]
    t = st.title_fight(FakeRace("Race"), drivers, True)
    alive = [c["code"] for c in t["contenders"] if c["alive"]]
    assert alive == ["NOR"] and t["champion"] == "NOR"            # 425 vs 360 + 50 left
    dropped = [c["code"] for c in t["contenders"] if c["was"] and not c["alive"]]
    assert dropped == ["PIA", "VER"]


def test_still_open():
    t = _st().title_fight(FakeRace("Race"), [_d("NOR", 400, 0), _d("PIA", 390, 25)], True)
    assert t["champion"] is None and all(c["alive"] for c in t["contenders"])
