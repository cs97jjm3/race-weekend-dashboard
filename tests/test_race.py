"""The race logic: reading F1's feed and turning it into the screen and the alerts."""
from conftest import play
from race import merge


def titles(race):
    return [e["title"] for e in race.sent]


def test_merge_folds_small_changes_into_the_full_picture():
    base = {"Lines": {"1": {"Position": "1", "Sectors": [{"Value": "30.1"}, {"Value": ""}]}}}
    merge(base, {"Lines": {"1": {"Sectors": {"1": {"Value": "34.2"}}}, "44": {"Position": "2"}}})
    assert base["Lines"]["1"]["Sectors"][1]["Value"] == "34.2"
    assert base["Lines"]["1"]["Position"] == "1"
    assert base["Lines"]["44"]["Position"] == "2"


def test_live_feed_marker_is_not_mistaken_for_a_driver(new_race):
    # the live feed slips "_kf": true in among the drivers and timing lines
    new_race.update("DriverList", {"1": {"Tla": "VER", "LastName": "Verstappen"}, "_kf": True})
    new_race.update("TimingData", {"Lines": {"1": {"Position": "1"}, "_kf": True}})
    assert list(new_race.drivers()) == ["1"]
    assert list(new_race.lines()) == ["1"]


def test_demo_race_plays_through_to_the_flag(new_race):
    play(new_race, "demo")
    v = new_race.view()
    assert new_race.finished_sent
    assert v["rows"][0]["tla"] == "LEC"
    for t in ("LIGHTS OUT", "SAFETY CAR", "PIT STOP", "RETIRED", "CHEQUERED FLAG"):
        assert t in titles(new_race), t
    bor = next(r for r in v["rows"] if r["tla"] == "BOR")
    assert bor["out"]


def test_finish_message_lists_each_persons_drivers(new_race):
    play(new_race, "demo")
    james = [e for e in new_race.sent if e["title"] == "CHEQUERED FLAG" and e["who"] == ["James"]][0]
    assert "P1 Leclerc" in james["text"] and "P11 Hamilton" in james["text"]
    ed = [e for e in new_race.sent if e["title"] == "CHEQUERED FLAG" and e["who"] == ["Ed"]][0]
    assert "P4 Hulkenberg" in ed["text"]


def test_stewards_penalty_shows_and_badges_the_driver(new_race):
    play(new_race, "demo")
    pen = [e for e in new_race.sent if e["title"] == "STEWARDS · PENALTY"]
    assert pen and "5 second time penalty for Verstappen" in pen[0]["text"]
    ver = next(r for r in new_race.view()["rows"] if r["tla"] == "VER")
    assert ver["pens"] == ["+5s"]


def _stewards(race, messages):
    race.update("SessionInfo", {"Path": "2026/x/Race/", "Type": "Race", "Name": "Race"})
    race.update("DriverList", {
        "1": {"Tla": "VER", "LastName": "Verstappen", "TeamName": "Red Bull Racing"},
        "4": {"Tla": "NOR", "LastName": "Norris", "TeamName": "McLaren"},
        "27": {"Tla": "HUL", "LastName": "Hulkenberg", "TeamName": "Audi"}})
    for i, m in enumerate(messages):
        race.update("RaceControlMessages",
                    {"Messages": [{"Message": m}]} if i == 0 else {"Messages": {str(i): {"Message": m}}})


def test_an_incident_is_followed_from_noted_to_decision(new_race):
    _stewards(new_race, [
        "FIA STEWARDS: TURN 1 INCIDENT INVOLVING CARS 1 (VER) AND 4 (NOR) NOTED - CAUSING A COLLISION",
        "FIA STEWARDS: TURN 1 INCIDENT INVOLVING CARS 1 (VER) AND 4 (NOR) UNDER INVESTIGATION - CAUSING A COLLISION",
        "FIA STEWARDS: TURN 1 INCIDENT INVOLVING CARS 1 (VER) AND 4 (NOR) REVIEWED NO FURTHER INVESTIGATION"])
    assert titles(new_race) == ["STEWARDS · NOTED", "STEWARDS · UNDER INVESTIGATION", "STEWARDS · NO FURTHER ACTION"]
    assert "Verstappen and Norris" in new_race.sent[0]["text"]
    assert new_race.penalties.get("1", []) == []


def test_drive_through_badge_goes_once_served(new_race):
    _stewards(new_race, [
        "FIA STEWARDS: DRIVE THROUGH PENALTY FOR CAR 27 (HUL) - SPEEDING IN THE PIT LANE"])
    assert new_race.penalties["27"] == ["DT"]
    assert new_race.sent[-1]["who"] == ["Ed"] and new_race.sent[-1]["loud"]
    new_race.update("RaceControlMessages", {"Messages": {"1": {"Message": "FIA STEWARDS: DRIVE THROUGH PENALTY SERVED BY CAR 27 (HUL)"}}})
    assert new_race.penalties["27"] == []


def test_track_limits_only_shown_for_followed_drivers(new_race):
    _stewards(new_race, [
        "CAR 4 (NOR) TIME 1:35.000 DELETED - TRACK LIMITS AT TURN 19",
        "CAR 1 (VER) TIME 1:35.860 DELETED - TRACK LIMITS AT TURN 19"])
    assert titles(new_race) == ["RACE CONTROL"]
    assert new_race.sent[0]["text"].startswith("Verstappen time 1:35.860 deleted")


def test_a_new_session_starts_the_timing_afresh(new_race):
    play(new_race, "demo")
    assert new_race.penalties
    new_race.update("SessionInfo", {"Path": "2026/x/Next/", "Type": "Practice", "Name": "Practice 3"})
    assert new_race.lines() == {}
    assert new_race.penalties == {}
    assert new_race.drivers()          # the driver list is kept
    assert new_race.events             # and so is the feed history


def test_qualifying_cut_lines_and_drop_zone(new_race):
    play(new_race, "demo-quali")
    v = new_race.view()
    assert new_race.is_quali()
    assert new_race._entries() == [22, 16, 10]
    out = [r["tla"] for r in v["rows"] if r.get("knockedOut")]
    assert len(out) == 12                       # six out in Q1, six more in Q2
    assert v["rows"][0]["tla"] == "LEC"
    texts = [e["text"] for e in new_race.sent]
    assert "Bortoleto is out in Q1, P20" in texts
    assert any(t.startswith("Hulkenberg is in the drop zone") for t in texts)


def test_investigated_after_the_session_is_labelled_so(new_race):
    # real wording from Austin FP3
    _stewards(new_race, [
        "TURN 12 INCIDENT INVOLVING CARS 5 (BOR) AND 44 (HAM) NOTED - IMPEDING (13:22:03)",
        "FIA STEWARDS: TURN 12 INCIDENT INVOLVING CARS 5 (BOR) AND 44 (HAM) WILL BE INVESTIGATED AFTER THE SESSION - IMPEDING (13:22:03)"])
    new_race.update("DriverList", {"5": {"Tla": "BOR", "LastName": "Bortoleto", "TeamName": "Audi"},
                                   "44": {"Tla": "HAM", "LastName": "Hamilton", "TeamName": "Ferrari"}})
    assert titles(new_race)[-1] == "STEWARDS · AFTER THE SESSION"
