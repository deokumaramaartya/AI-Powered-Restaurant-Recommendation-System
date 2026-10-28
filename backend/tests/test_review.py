import csv
import json

import pytest

from evals import review
from evals.judge import DIMENSIONS, RubricScore

REQUEST = {"location": "Jayanagar", "budget": "medium", "min_rating": 4.0, "top_k": 2,
           "extra_preferences": "family-friendly"}


def report(*runs):
    return {"header": {"mode": "live"}, "queries": list(runs)}


def run(query_id, ids, fallback_ids=(), category="soft_preferences", source="llm"):
    return {"query_id": query_id, "category": category, "source": source, "error": None, "request": REQUEST,
            "ids": list(ids), "fallback_ids": list(fallback_ids), "summary": "Two family picks.",
            "results": [{"id": rid, "name": rid, "explanation": f"Why {rid}."} for rid in ids]}


def write_sheet(path, columns, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return str(path)


def test_relevance_sheet_mixes_both_rankers_and_hides_the_source(store):
    rows = review.relevance_rows(report(run("G-14", ["jay-1", "jay-2"], ["jay-2", "jay-3"])), store, set())
    assert sorted(row["restaurant_id"] for row in rows) == ["jay-1", "jay-2", "jay-3"]
    assert not any("fallback" in json.dumps(row) or "llm" in json.dumps(row) for row in rows)
    assert "₹" in rows[0]["key_facts"] and "family-friendly" in rows[0]["request"]


def test_relevance_sheet_skips_injection_queries_and_already_graded_restaurants(store):
    rows = review.relevance_rows(
        report(run("G-14", ["jay-1", "jay-2"]), run("G-20", ["btm-1"], category="injection")),
        store, {("G-14", "jay-1")},
    )
    assert [(row["query_id"], row["restaurant_id"]) for row in rows] == [("G-14", "jay-2")]


@pytest.mark.parametrize(
    ("grades", "expected"),
    [(("2", "3", ""), (2.5, ["r1", "r2"])), (("1", "", ""), (1.0, ["r1"])), (("0", "3", "1"), (1.0, ["r1", "r2"])),
     (("", "", ""), None)],
)
def test_grades_are_settled_per_the_review_rules(grades, expected):
    row = dict(zip(("reviewer_1_grade", "reviewer_2_grade", "agreed_grade"), grades))
    assert review.settle_grade(row, "row") == expected


@pytest.mark.parametrize("grades", [("0", "3", ""), ("4", "", ""), ("good", "", "")])
def test_a_two_point_gap_or_bad_value_needs_fixing(grades):
    row = dict(zip(("reviewer_1_grade", "reviewer_2_grade", "agreed_grade"), grades))
    with pytest.raises(ValueError):
        review.settle_grade(row, "row")


def test_import_labels_merges_into_labels_jsonl(tmp_path):
    labels = tmp_path / "labels.jsonl"
    labels.write_text(json.dumps({"query_id": "G-14", "restaurant_id": "jay-1", "grade": 1.0,
                                  "reviewers": ["r1"], "note": ""}) + "\n", encoding="utf-8")
    sheet = write_sheet(tmp_path / "sheet.csv", review.RELEVANCE_COLUMNS, [
        {"query_id": "G-14", "restaurant_id": "jay-1", "reviewer_1_grade": "3", "reviewer_2_grade": "3"},
        {"query_id": "G-14", "restaurant_id": "jay-2", "reviewer_1_grade": "2", "notes": " ok "},
        {"query_id": "G-14", "restaurant_id": "jay-3"},
    ])
    assert review.import_labels(sheet, labels) == 2
    rows = [json.loads(line) for line in labels.read_text(encoding="utf-8").splitlines()]
    assert [(row["restaurant_id"], row["grade"]) for row in rows] == [("jay-1", 3.0), ("jay-2", 2.0)]
    assert rows[1]["note"] == "ok"


def test_import_stops_without_writing_when_a_row_needs_agreement(tmp_path):
    labels = tmp_path / "labels.jsonl"
    sheet = write_sheet(tmp_path / "sheet.csv", review.RELEVANCE_COLUMNS, [
        {"query_id": "G-14", "restaurant_id": "jay-1", "reviewer_1_grade": "2"},
        {"query_id": "G-14", "restaurant_id": "jay-2", "reviewer_1_grade": "0", "reviewer_2_grade": "3"},
    ])
    with pytest.raises(SystemExit, match="agreed_grade"):
        review.import_labels(sheet, labels)
    assert not labels.exists()


def test_calibration_sheet_takes_explanations_round_robin(store):
    rows = review.calibration_rows(
        report(run("G-14", ["jay-1", "jay-2"]), run("G-03", ["jay-3", "jay-4"]),
               run("G-27", ["ind-1"], source="fallback")),
        store, count=3,
    )
    assert [(row["query_id"], row["restaurant_id"]) for row in rows] == [
        ("G-14", "jay-1"), ("G-03", "jay-3"), ("G-14", "jay-2")]
    assert json.loads(rows[0]["request_json"]) == REQUEST


def test_calibrate_records_agreement_for_the_judge_model(store, tmp_path):
    class FakeJudge:
        model = "fake-judge"

        def score(self, request, items, summary, relaxations=None):
            return [RubricScore(item["restaurant"]["id"], item["explanation"], {d: 4 for d in DIMENSIONS})
                    for item in items]

    rows = review.calibration_rows(report(run("G-14", ["jay-1", "jay-2"])), store)
    rows[0].update({f"r1_{d}": "5" for d in DIMENSIONS} | {f"r2_{d}": "4" for d in DIMENSIONS})
    rows[1].update({f"r1_{d}": "1" for d in DIMENSIONS})
    sheet = write_sheet(tmp_path / "calibration.csv", review.calibration_columns(), rows)
    result = review.calibrate(sheet, FakeJudge(), store, tmp_path / "calibration.json")
    assert result["agreement"] == {d: 0.5 for d in DIMENSIONS}
    assert result["passed"] is False
    saved = json.loads((tmp_path / "calibration.json").read_text(encoding="utf-8"))
    assert saved["judge_model"] == "fake-judge" and saved["explanations"] == 2
