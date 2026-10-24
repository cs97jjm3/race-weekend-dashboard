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
    c = fia.Checker(new_race, lambda t, u, info: posted.append((t, u)), fetch=lambda: PAGE, fetch_pdf=lambda u: b"")
    assert c.check("United States Grand Prix", "Austin") == 3
    assert posted[-1][0].startswith("Infringement - Car 44")          # newest last, so it lands on top
    assert c.check("United States Grand Prix", "Austin") == 0          # nothing twice
    assert fia.Checker(new_race, lambda t, u, info: posted.append(t), fetch=lambda: PAGE).check("United States Grand Prix") == 0


def test_car_numbers_get_names(new_race):
    new_race.update("DriverList", {"44": {"Tla": "HAM", "LastName": "Hamilton", "TeamName": "Ferrari"}})
    text, who = fia.car_names(new_race, "Infringement - Car 44 - Impeding car 5")
    assert text == "Infringement - Car 44 (Hamilton) - Impeding car 5" and who == {"James"}


# Doc 15 from Austin 2026, as pypdf reads it: sometimes the label and its words share a line,
# sometimes the label sits on a line of its own.
DOC15 = """2026 UNITED STATES GRAND PRIX
From The Stewards
Document 15
Time 13:37
The Stewards, having received a report from the Technical Delegate (document 13), have
considered the following matter and determine the following:
No / Driver 5 - Gabriel Bortoleto
Competitor Audi Revolut F1 Team
Time 12:31
Fact The following Power Unit elements have been used:
4th Energy Store (ES)
4th Control Electronics Unit (PU-CE)
Infringement Breaches of Article B8.2.2 (read with Article B8.2.3) of the FIA F1 Regulations.
Decision Drop of 20 grid positions for the next Race in which the driver participates.
Reason The penalty is imposed in accordance with Article B8.2.8a of the FIA F1 Regulations.
The use of each additional element in this case carries a 10 grid place penalty
therefore there is an accumulation of 20 places.
Competitors are reminded that they have the right to appeal certain decisions of the
Stewards, in accordance with Article 15 of the FIA International Sporting Code.
Garry Connelly Matthew Selley
"""


def test_decision_pdf_is_read():
    f = fia.parse_decision(DOC15)
    assert f["No / Driver"] == "5 - Gabriel Bortoleto" and fia.driver_number(f) == "5"
    assert f["Decision"] == "Drop of 20 grid positions for the next Race in which the driver participates."
    assert f["Reason"].endswith("accumulation of 20 places.")
    assert "Competitor" in f and "Time" not in f["Competitor"]
    lines = fia.summary(f)
    assert lines[0].startswith("Decision: Drop of 20 grid positions")
    assert lines[1].startswith("What happened: The following Power Unit elements")


def test_labels_on_their_own_lines():
    text = DOC15.replace("Decision Drop", "Decision\nDrop").replace("No / Driver 5", "No / Driver\n5")
    f = fia.parse_decision(text)
    assert fia.driver_number(f) == "5" and f["Decision"].startswith("Drop of 20")


def test_documents_without_a_decision_give_nothing():
    assert fia.parse_decision("Free Practice 2 deleted lap times\nCar Driver Lap Time") == {}
    assert fia.summary({}) == []


def test_decision_details_reach_the_feed(tmp_path, monkeypatch, new_race):
    monkeypatch.setattr(fia, "SEEN_FILE", str(tmp_path / "seen.json"))
    monkeypatch.setattr(fia.threading, "Thread", lambda **k: type("T", (), {"start": lambda self: None})())
    monkeypatch.setattr(fia, "pdf_text", lambda data: DOC15)
    posted = []
    c = fia.Checker(new_race, lambda t, u, info: posted.append(info), fetch=lambda: PAGE, fetch_pdf=lambda u: b"%PDF")
    c.check("United States Grand Prix", "Austin")
    assert posted[0]["lines"][0].startswith("Decision: Drop of 20")
