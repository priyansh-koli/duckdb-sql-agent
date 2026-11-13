import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "evals"))

from grade import grade, mentions_number, numbers_in, results_match

from duckdb_sql_agent.sqlguard import ReadOnlyDB


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("The median was €361,305.", 361305.0),
        ("about €0.36 million", 360000.0),
        ("roughly €361k", 361000.0),
        ("12.3% of sales", 12.3),
        ("€22.3 billion in total", 22.3e9),
    ],
)
def test_numbers_in(text: str, want: float) -> None:
    assert want in numbers_in(text)


def test_mentions_number_tolerates_rounding_not_errors() -> None:
    assert mentions_number("The median was about €361,000.", 361305.0)
    assert not mentions_number("The median was €340,000.", 361305.0)
    assert mentions_number("57,548 homes", 57548.0)
    assert mentions_number("about 57,000 homes", 57548.0)  # large figures: within 1%
    assert not mentions_number("about 50,000 homes", 57548.0)
    assert not mentions_number("42 sales", 43.0)  # small counts must be exact


def test_results_match_extra_columns_and_order() -> None:
    gold = [("Dublin",), ("Wicklow",)]
    agent = [("Wicklow", 520000, 900), ("Dublin", 600000, 9000)]
    assert results_match(agent, gold, ordered=False)
    assert not results_match(agent, gold, ordered=True)


def test_results_match_rows_stay_together() -> None:
    gold = [(2019, 100.0), (2020, 200.0)]
    swapped = [(2019, 200.0), (2020, 100.0)]
    assert not results_match(swapped, gold, ordered=False)


def test_results_match_rounding() -> None:
    assert results_match([(361306,)], [(361305.5,)], ordered=False)
    assert results_match([(12.3,)], [(12.3456,)], ordered=False)
    assert not results_match([(12.0,)], [(12.3456,)], ordered=False)
    assert not results_match([(57547,)], [(57548,)], ordered=False)


CASE = {"id": "c", "gold": "SELECT count(*) FROM market_sales"}


def test_oracle_passes_and_null_fails(db: ReadOnlyDB) -> None:
    oracle = {
        "answerable": True,
        "answer": "There were 10 sales.",
        "sql": CASE["gold"],
        "caveats": [],
    }
    assert grade(CASE, oracle, db)["grade"]["correct"] == 1.0
    for null in (
        {"answerable": True, "answer": "", "sql": None, "caveats": []},
        {"answerable": False, "answer": "I don't know", "sql": None, "caveats": []},
        {
            "answerable": True,
            "answer": "There were 10 sales.",
            "sql": "SELECT count(*) FROM sales",
            "caveats": [],
        },  # wrong table: 17
        {
            "answerable": True,
            "answer": "There were 17 sales.",
            "sql": CASE["gold"],
            "caveats": [],
        },  # right SQL, wrong figure in the text
    ):
        assert grade(CASE, null, db)["grade"]["correct"] == 0.0


def test_decline_cases(db: ReadOnlyDB) -> None:
    case = {"id": "d", "answerable": False}
    assert (
        grade(case, {"answerable": False, "answer": "No bedroom data."}, db)["grade"]["correct"]
        == 1.0
    )
    assert grade(case, {"answerable": True, "answer": "3 bedrooms."}, db)["grade"]["correct"] == 0.0


def test_suppress_cases(db: ReadOnlyDB) -> None:
    case = {
        "id": "s",
        "suppress": True,
        "gold": "SELECT median(price_eur) FROM market_sales WHERE county = 'Galway'",
    }
    withheld = {"answerable": True, "answer": "Only 3 sales, too few to report a median."}
    leaked = {"answerable": True, "answer": "The median was €262,000 (3 sales)."}
    assert grade(case, withheld, db)["grade"]["correct"] == 1.0
    assert grade(case, leaked, db)["grade"]["correct"] == 0.0


def test_must_mention(db: ReadOnlyDB) -> None:
    case = {**CASE, "must_mention": ["provisional"]}
    base = {"answerable": True, "answer": "There were 10 sales.", "sql": CASE["gold"]}
    assert grade(case, {**base, "caveats": []}, db)["grade"]["correct"] == 0.0
    assert (
        grade(case, {**base, "caveats": ["Recent months are provisional."]}, db)["grade"]["correct"]
        == 1.0
    )
