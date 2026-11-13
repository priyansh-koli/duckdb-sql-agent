from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from ppr_ask.build import HeaderMismatchError, build

FIXTURE = Path(__file__).parent / "fixtures" / "ppr_sample.csv"


def q(path: Path, sql: str) -> list[tuple[object, ...]]:
    con = duckdb.connect(str(path), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


# sale_id is the line number in the file: the fixture keeps 17 real rows on lines 2-18.


def test_every_row_loads_and_raw_is_kept(sample_db: Path) -> None:
    assert q(sample_db, "SELECT count(*) FROM sales_raw") == [(17,)]
    assert q(sample_db, "SELECT count(*) FROM sales") == [(17,)]
    # the euro sign survives cp1252 decoding in the raw field
    raw = q(sample_db, "SELECT raw_price FROM sales_raw WHERE line_no = 17")
    assert raw == [("€275,000.00",)]


def test_prices_are_exact_decimals(sample_db: Path) -> None:
    assert q(sample_db, "SELECT price_eur FROM sales WHERE sale_id = 2") == [(Decimal("74889.87"),)]


def test_irish_and_mojibake_descriptions_are_new(sample_db: Path) -> None:
    # 'Teach/Árasán Cónaithe Nua' and the mojibake 'Teach/?ras?n C?naithe Nua'
    rows = q(sample_db, "SELECT is_new FROM sales WHERE sale_id IN (2, 3)")
    assert rows == [(True,), (True,)]


def test_size_band_and_eircode(sample_db: Path) -> None:
    assert q(sample_db, "SELECT size_band FROM sales WHERE sale_id = 4") == [("125 sq m or more",)]
    assert q(sample_db, "SELECT eircode_routing_key FROM sales WHERE sale_id = 17") == [("P51",)]


def test_duplicate_flags_only_the_repeat(sample_db: Path) -> None:
    rows = q(
        sample_db,
        "SELECT sale_id, is_possible_duplicate FROM sales WHERE sale_id IN (10, 11) ORDER BY 1",
    )
    assert rows == [(10, False), (11, True)]


def test_bulk_groups(sample_db: Path) -> None:
    rows = q(sample_db, "SELECT sale_id, bulk_group_size FROM sales WHERE is_bulk ORDER BY 1")
    # three Dublin units at EUR 137,303 on one day, and two units at EUR 7.3M
    assert rows == [(6, 3), (7, 3), (8, 3), (15, 2), (16, 2)]


def test_market_sales_excludes_flagged_rows(sample_db: Path) -> None:
    # 17 - 5 bulk - 1 duplicate - 1 not full market price
    assert q(sample_db, "SELECT count(*) FROM market_sales") == [(10,)]


def test_dataset_info(sample_db: Path) -> None:
    rows = q(sample_db, "SELECT last_sale_date::VARCHAR FROM dataset_info")
    assert rows == [("2025-01-03",)]


def test_header_change_stops_the_build(tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_bytes(FIXTURE.read_bytes().replace(b"Eircode", b"Postcode", 1))
    with pytest.raises(HeaderMismatchError):
        build(bad, tmp_path / "x.duckdb")
