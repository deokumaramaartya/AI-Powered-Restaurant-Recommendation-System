import pandas as pd

from evals.data_profile import evaluate_frame, write_report


def restaurant_row(**overrides):
    row = {
        "id": "abc",
        "name": "Jalsa",
        "location": "BTM",
        "area": "BTM",
        "address": "1 Road",
        "cuisines": ["North Indian"],
        "rating": 4.1,
        "votes": 10,
        "cost_for_two": 400,
        "rest_type": "Casual Dining",
        "listing_types": ["Buffet"],
        "online_order": True,
        "book_table": False,
        "dish_liked": ["Pasta"],
    }
    row.update(overrides)
    return row


def balanced_frame():
    rows = []
    for index in range(4):
        rows.append(restaurant_row(id=f"low-{index}", name=f"Low {index}", location="BTM", cost_for_two=400))
    for index in range(4):
        rows.append(restaurant_row(id=f"mid-{index}", name=f"Mid {index}", location="HSR", cost_for_two=800))
    for index in range(2):
        rows.append(
            restaurant_row(id=f"high-{index}", name=f"High {index}", location="Indiranagar", cost_for_two=2000)
        )
    return pd.DataFrame(rows)


def test_profile_passes_a_clean_balanced_frame():
    """A null, a duplicate id, a stored tier, or a renamed acronym would be a bad dataset."""
    result = evaluate_frame(balanced_frame(), low_max=500, medium_max=1200)
    assert result.passed
    assert result.failures == []


def test_profile_fails_null_required_field_duplicate_id_and_stored_tier():
    """Those three defects are the Phase 1 acceptance failures."""
    frame = balanced_frame()
    frame.loc[0, "name"] = None
    frame.loc[1, "id"] = frame.loc[0, "id"]
    frame["budget_tier"] = "low"
    result = evaluate_frame(frame, low_max=500, medium_max=1200)
    assert not result.passed
    text = " ".join(result.failures)
    assert "name" in text
    assert "duplicate" in text
    assert "budget_tier" in text


def test_profile_fails_title_cased_acronyms_and_a_tiny_tier():
    """BTM stored as Btm, or a tier under 10%, must fail the Phase 1 gate."""
    frame = balanced_frame()
    frame["location"] = frame["location"].replace({"BTM": "Btm", "HSR": "Hsr"})
    frame["cost_for_two"] = 400
    result = evaluate_frame(frame, low_max=500, medium_max=1200)
    assert not result.passed
    text = " ".join(result.failures)
    assert "BTM" in text
    assert "HSR" in text
    assert "medium" in text


def test_write_report_saves_markdown(tmp_path):
    """The exit check needs the summary on disk at evals/reports/phase1-data.md."""
    destination = tmp_path / "phase1-data.md"
    result = write_report(balanced_frame(), destination, low_max=500, medium_max=1200)
    text = destination.read_text(encoding="utf-8")
    assert result.passed
    assert "Rows after cleaning" in text
    assert "BTM" in text
    assert "low" in text
