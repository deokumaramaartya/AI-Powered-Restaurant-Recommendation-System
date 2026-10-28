import pandas as pd
import pytest

from evals.data_profile import write_report
from scripts.ingest import (
    IngestError,
    clean_dataframe,
    make_id,
    normalize_key,
    parse_cost,
    parse_rating,
    read_rows_before,
    run_ingest,
    split_list,
    summarize,
    to_bool,
)


def raw_row(**overrides):
    row = {
        "url": "http://example",
        "address": "942, 21st Main Road",
        "name": "Jalsa",
        "online_order": "Yes",
        "book_table": "No",
        "rate": "4.1/5",
        "votes": 100,
        "phone": "080 42297555",
        "location": "Banashankari",
        "rest_type": "Casual Dining",
        "dish_liked": "Pasta, Lunch Buffet",
        "cuisines": "North Indian, Chinese",
        "approx_cost(for two people)": "800",
        "reviews_list": "[]",
        "menu_item": "[]",
        "listed_in(type)": "Buffet",
        "listed_in(city)": "Banashankari",
    }
    row.update(overrides)
    return row


def test_parse_rating_strips_slash_five_including_a_space():
    """Leaving the slash, or failing on the space in '4.1 /5', would store a string or null."""
    assert parse_rating("4.1/5") == 4.1
    assert parse_rating("4.1 /5") == 4.1
    assert parse_rating(" 3.8/5 ") == 3.8
    assert parse_rating("4.1") == 4.1


@pytest.mark.parametrize("value", ["NEW", "-", "", None, "   "])
def test_parse_rating_missing_markers_are_null(value):
    """Treating NEW, dash, or blank as a number would keep unrated rows."""
    assert parse_rating(value) is None


@pytest.mark.parametrize("value", ["4.1/10", "abc", "6.2", "-1"])
def test_parse_rating_out_of_range_or_other_scale_is_null(value):
    """Accepting another scale or garbage would put a bad rating in the file."""
    assert parse_rating(value) is None


def test_parse_cost_strips_commas_and_extracts_a_number():
    """Keeping the comma, or failing on 'Rs 800' / '800.0', would null a real cost."""
    assert parse_cost("800") == 800
    assert parse_cost("1,200") == 1200
    assert parse_cost("approx 500") == 500
    assert parse_cost("Rs 800") == 800
    assert parse_cost("800.0") == 800


@pytest.mark.parametrize("value", ["", None, "free", "0", "-5", 0, -5])
def test_parse_cost_empty_or_non_positive_is_null(value):
    """A zero or negative cost would otherwise fall into the low tier."""
    assert parse_cost(value) is None


def test_to_bool_is_case_insensitive_and_null_is_false():
    """A case-sensitive check would turn 'yes' into false; null must not raise."""
    assert to_bool("Yes") is True
    assert to_bool("yes") is True
    assert to_bool("YES") is True
    assert to_bool("No") is False
    assert to_bool(None) is False
    assert to_bool("maybe") is False


def test_split_list_drops_empty_tokens_and_dedupes_case_insensitively():
    """Empty tokens or 'Fast Food' plus 'fast food' would pollute cuisine lists."""
    assert split_list("North Indian, , Chinese,") == ["North Indian", "Chinese"]
    assert split_list("Fast Food, fast food") == ["Fast Food"]
    assert split_list(None) == []
    assert split_list("") == []


def test_normalize_key_folds_case_and_collapses_whitespace():
    """A key that keeps case or extra spaces would miss duplicate restaurants."""
    assert normalize_key("  Jalsa   Bar ") == normalize_key("jalsa bar")
    assert normalize_key(None) == ""


def test_make_id_is_stable_for_the_same_normalized_name_and_address():
    """A random id, or one that keeps whitespace and case, would split one restaurant."""
    first = make_id("Jalsa", "1 Main Road")
    assert make_id("Jalsa", "1 Main Road") == first
    assert make_id("  jalsa ", "1   main   road") == first
    assert make_id("Jalsa", "2 Main Road") != first
    assert len(first) >= 32


def test_location_keeps_acronym_casing():
    """Title-casing location would turn BTM into Btm and HSR into Hsr."""
    frame = pd.DataFrame(
        [
            raw_row(name="A", address="1 Road", location="BTM"),
            raw_row(name="B", address="2 Road", location="HSR"),
        ]
    )
    cleaned = clean_dataframe(frame)
    assert set(cleaned["location"]) == {"BTM", "HSR"}


def test_dedup_merges_listing_types_for_the_same_restaurant():
    """Keeping one row per listing type would duplicate the restaurant."""
    frame = pd.DataFrame(
        [
            raw_row(**{"listed_in(type)": "Buffet", "votes": 10}),
            raw_row(**{"listed_in(type)": "Dine-out", "votes": 50}),
        ]
    )
    cleaned = clean_dataframe(frame)
    assert len(cleaned) == 1
    assert cleaned.iloc[0]["listing_types"] == ["Buffet", "Dine-out"]
    assert cleaned.iloc[0]["votes"] == 50
    assert "budget_tier" not in cleaned.columns


def test_dedup_collapses_name_and_address_that_differ_only_by_space_or_case():
    """Comparing raw name and address would keep both rows."""
    frame = pd.DataFrame(
        [
            raw_row(name="Jalsa", address="1 Main Road", votes=10, **{"listed_in(type)": "Buffet"}),
            raw_row(
                name="  jalsa ",
                address="1   main   road",
                votes=40,
                **{"listed_in(type)": "Delivery"},
            ),
        ]
    )
    cleaned = clean_dataframe(frame)
    assert len(cleaned) == 1
    assert cleaned.iloc[0]["name"] == "jalsa"
    assert cleaned.iloc[0]["votes"] == 40
    assert cleaned.iloc[0]["listing_types"] == ["Buffet", "Delivery"]


def test_dedup_takes_conflicting_fields_from_the_highest_votes_row():
    """Averaging or keeping the first row would store the low-vote rating and area."""
    frame = pd.DataFrame(
        [
            raw_row(
                rate="3.1/5",
                votes=10,
                rest_type="Cafe",
                dish_liked="Tea",
                **{"listed_in(city)": "South", "listed_in(type)": "Delivery"},
            ),
            raw_row(
                rate="4.6/5",
                votes=200,
                rest_type="Fine Dining",
                dish_liked="Pasta",
                **{"listed_in(city)": "North", "listed_in(type)": "Buffet"},
            ),
        ]
    )
    cleaned = clean_dataframe(frame)
    row = cleaned.iloc[0]
    assert row["rating"] == 4.6
    assert row["area"] == "North"
    assert row["rest_type"] == "Fine Dining"
    assert row["dish_liked"] == ["Pasta"]
    assert row["listing_types"] == ["Delivery", "Buffet"]


def test_same_name_at_different_addresses_stays_two_restaurants():
    """Hashing only the name would collapse a chain into one id."""
    frame = pd.DataFrame(
        [
            raw_row(name="Cafe", address="1 First Road"),
            raw_row(name="Cafe", address="9 Second Road"),
        ]
    )
    cleaned = clean_dataframe(frame)
    assert len(cleaned) == 2
    assert cleaned["id"].nunique() == 2


def test_rows_missing_required_fields_are_dropped():
    """Keeping a row with no location, cuisine, rating, or cost would leave nulls."""
    frame = pd.DataFrame(
        [
            raw_row(name="Kept", address="1 Road"),
            raw_row(name="No Location", address="2 Road", location=None),
            raw_row(name="No Cuisine", address="3 Road", cuisines=None),
            raw_row(name="New Place", address="4 Road", rate="NEW"),
            raw_row(name="No Cost", address="5 Road", **{"approx_cost(for two people)": ""}),
            raw_row(name=None, address="6 Road"),
            raw_row(name="Blank Cuisine", address="7 Road", cuisines=" , "),
        ]
    )
    cleaned = clean_dataframe(frame)
    assert list(cleaned["name"]) == ["Kept"]
    assert cleaned["rating"].notna().all()
    assert cleaned["cost_for_two"].notna().all()


def test_null_rest_type_is_kept_and_null_dish_liked_is_an_empty_list():
    """Dropping a null rest_type, or storing dish_liked as NaN, would break the model."""
    frame = pd.DataFrame([raw_row(rest_type=None, dish_liked=None)])
    cleaned = clean_dataframe(frame)
    assert len(cleaned) == 1
    assert cleaned.iloc[0]["rest_type"] is None
    assert cleaned.iloc[0]["dish_liked"] == []


def test_yes_no_flags_and_text_are_normalized():
    """Leaving 'Yes' as a string, or keeping double spaces, would fail later filters."""
    frame = pd.DataFrame(
        [
            raw_row(
                name="  Cafe   Rio ",
                online_order="yes",
                book_table="YES",
                location="  BTM  ",
            )
        ]
    )
    row = clean_dataframe(frame).iloc[0]
    assert row["name"] == "Cafe Rio"
    assert row["location"] == "BTM"
    assert row["online_order"] is True
    assert row["book_table"] is True


def test_cuisines_share_one_spelling_across_rows():
    """Leaving both 'Fast Food' and 'fast food' would duplicate the later dropdown."""
    frame = pd.DataFrame(
        [
            raw_row(name="A", address="1 Road", cuisines="fast food"),
            raw_row(name="B", address="2 Road", cuisines="fast food"),
            raw_row(name="C", address="3 Road", cuisines="Fast Food"),
        ]
    )
    cleaned = clean_dataframe(frame)
    spellings = {item for items in cleaned["cuisines"] for item in items}
    assert spellings == {"fast food"}


def test_non_ascii_name_is_stored():
    """A crash or dropped character on non-ASCII names would lose the restaurant."""
    frame = pd.DataFrame([raw_row(name="CafÃ©")])
    cleaned = clean_dataframe(frame)
    assert cleaned.iloc[0]["name"] == "CafÃ©"


def test_missing_raw_columns_are_named():
    """A generic failure would hide which upstream column disappeared."""
    frame = pd.DataFrame([{"name": "Jalsa", "address": "1 Road"}])
    with pytest.raises(IngestError) as caught:
        clean_dataframe(frame)
    message = str(caught.value)
    assert "location" in message
    assert "cuisines" in message
    assert "rate" in message


def test_cleaning_that_drops_every_row_does_not_replace_the_file(tmp_path):
    """Writing an empty Parquet file would make the server load zero restaurants."""
    output = tmp_path / "restaurants.parquet"
    output.write_bytes(b"existing-bytes")
    frame = pd.DataFrame([raw_row(rate="NEW")])
    with pytest.raises(IngestError, match="empty"):
        run_ingest(loader=lambda: frame, output_path=output)
    assert output.read_bytes() == b"existing-bytes"


def test_download_failure_leaves_the_existing_file(tmp_path):
    """A failed Hugging Face download must not truncate a good Parquet file."""
    output = tmp_path / "restaurants.parquet"
    output.write_bytes(b"existing-bytes")

    def boom():
        raise RuntimeError("offline")

    with pytest.raises(IngestError, match="load"):
        run_ingest(loader=boom, output_path=output)
    assert output.read_bytes() == b"existing-bytes"


def test_run_ingest_writes_parquet_without_budget_tier(tmp_path):
    """The written file must reload with required values and no budget_tier column."""
    output = tmp_path / "restaurants.parquet"
    frame = pd.DataFrame(
        [
            raw_row(name="Low", address="1 Road", **{"approx_cost(for two people)": "400"}),
            raw_row(name="Mid", address="2 Road", **{"approx_cost(for two people)": "800"}),
            raw_row(name="High", address="3 Road", **{"approx_cost(for two people)": "2000"}),
        ]
    )
    stats = run_ingest(loader=lambda: frame, output_path=output, low_max=500, medium_max=1200)
    loaded = pd.read_parquet(output)
    assert "budget_tier" not in loaded.columns
    assert loaded["id"].is_unique
    assert loaded["name"].notna().all()
    assert loaded["location"].notna().all()
    assert list(loaded.iloc[0]["cuisines"]) == ["North Indian", "Chinese"]
    assert stats["rows_after"] == 3
    assert stats["rows_before"] == 3
    assert stats["budget_counts"] == {"low": 1, "medium": 1, "high": 1}
    assert stats["warnings"] == []
    assert not output.with_suffix(".parquet.tmp").exists()


def test_written_parquet_remembers_how_many_rows_were_loaded(tmp_path):
    """A later data profile must still report the pre-clean row count."""
    output = tmp_path / "restaurants.parquet"
    report = tmp_path / "phase1-data.md"
    frame = pd.DataFrame(
        [
            raw_row(name="Low", address="1 Road", location="BTM", **{"approx_cost(for two people)": "200"}),
            raw_row(name="Mid", address="2 Road", location="HSR", **{"approx_cost(for two people)": "400"}),
            raw_row(name="High", address="3 Road", location="Indiranagar", **{"approx_cost(for two people)": "900"}),
            raw_row(name="Unrated", address="4 Road", rate="NEW"),
        ]
    )
    run_ingest(loader=lambda: frame, output_path=output, low_max=300, medium_max=600)
    assert read_rows_before(output) == 4
    result = write_report(
        pd.read_parquet(output),
        report,
        low_max=300,
        medium_max=600,
        rows_before=read_rows_before(output),
    )
    assert "Rows before cleaning: 4" in result.markdown
    assert "Rows after cleaning: 3" in result.markdown


def test_nearly_empty_budget_tier_is_warned():
    """A tier under 10% must be called out so the thresholds can be tuned."""
    rows = [
        raw_row(name=f"Place {index}", address=f"{index} Road", **{"approx_cost(for two people)": "300"})
        for index in range(19)
    ]
    rows.append(raw_row(name="Pricey", address="99 Road", **{"approx_cost(for two people)": "2000"}))
    cleaned = clean_dataframe(pd.DataFrame(rows))
    stats = summarize(len(rows), cleaned, low_max=500, medium_max=1200)
    warning_text = " ".join(stats["warnings"])
    assert "medium" in warning_text
    assert "high" in warning_text
