"""Stewards' decisions from the FIA's documents page."""
import fia

PAGE = """
<div class="event">United States Grand Prix
<a href="/system/files/decision-document/2026_united_states_grand_prix_-_p1_and_p2_scrutineering.pdf">Doc 25</a>
<a href="https://www.fia.com/system/files/decision-document/2026_united_states_grand_prix_-_infringement_-_car_44_-_impeding_car_5.pdf">Doc 24</a>
<a href="/system/files/decision-document/2026_united_states_grand_prix_-_decision_-_car_63_-_alleged_forcing_another_driver_off_the_track.pdf">Doc 21</a>
<a href="/system/files/decision-document/2026_united_states_grand_prix_-_race_directors_competition_notes_v2.pdf">Doc 18</a>
<a href="/system/files/decision-document/2026_united_states_grand_prix_-_infringement_-_car_5_-_pu_elements.pdf">Doc 15</a>
</div>
<a href="/system/files/decision-document/2026_singapore_grand_prix_-_decision_-_car_1.pdf">old event</a>
<a href="/sites/default/files/decision-document/2023 United States Grand Prix - Infringement - Car 63 - Impeding of Car 16.pdf">2023</a>
"""


def test_only_this_events_decisions():
    docs = [t for u, t in fia.documents(PAGE, "United States Grand Prix", "Austin", 2026) if fia.is_decision(t)]
    assert docs == ["Infringement - Car 44 - Impeding car 5",
                    "Decision - Car 63 - Alleged forcing another driver off the track",
                    "Infringement - Car 5 - PU elements"]


def test_city_named_events():
    page = '<a href="/system/files/decision-document/2026_melbourne_event_-_summons_-_car_20_-_allegedly_impeded_car_7_in_fp.pdf">x</a>'
    docs = fia.documents(page, "Australian Grand Prix", "Melbourne", 2026)
    assert docs and docs[0][1] == "Summons - Car 20 - Allegedly impeded car 7 in FP"


def test_new_decisions_posted_once(tmp_path, monkeypatch, new_race):
    monkeypatch.setattr(fia, "SEEN_FILE", str(tmp_path / "seen.json"))
    monkeypatch.setattr(fia.threading, "Thread", lambda **k: type("T", (), {"start": lambda self: None})())
    posted = []
    c = fia.Checker(new_race, lambda t, u: posted.append((t, u)), fetch=lambda: PAGE)
    assert c.check("United States Grand Prix", "Austin") == 3
    assert posted[-1][0].startswith("Infringement - Car 44")          # newest last, so it lands on top
    assert c.check("United States Grand Prix", "Austin") == 0          # nothing twice
    assert fia.Checker(new_race, lambda t, u: posted.append(t), fetch=lambda: PAGE).check("United States Grand Prix") == 0


def test_car_numbers_get_names(new_race):
    new_race.update("DriverList", {"44": {"Tla": "HAM", "LastName": "Hamilton", "TeamName": "Ferrari"}})
    text, who = fia.car_names(new_race, "Infringement - Car 44 - Impeding car 5")
    assert text == "Infringement - Car 44 (Hamilton) - Impeding car 5" and who == {"James"}
