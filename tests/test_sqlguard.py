from pathlib import Path

import pytest

from duckdb_sql_agent.sqlguard import QueryRejected, ReadOnlyDB, check_sql


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE sales",
        "DELETE FROM sales",
        "UPDATE sales SET price_eur = 1",
        "INSERT INTO sales SELECT * FROM sales",
        "SELECT 1; SELECT 2",
        "ATTACH 'other.db'",
        "INSTALL httpfs",
        "COPY sales TO 'out.csv'",
        "SET lock_configuration = false",
        "PRAGMA version",
        "",
    ],
)
def test_rejects_anything_but_one_select(sql: str) -> None:
    with pytest.raises(QueryRejected):
        check_sql(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT count(*) FROM sales",
        "select 1;",
        "WITH x AS (SELECT 1 AS a) SELECT a FROM x",
        "-- a comment\nSELECT 1",
        "FROM sales SELECT count(*)",
    ],
)
def test_accepts_select(sql: str) -> None:
    assert check_sql(sql)


def test_file_access_is_blocked_inside_duckdb(db: ReadOnlyDB) -> None:
    r = db.run("SELECT * FROM read_csv('/etc/passwd')")
    assert r.error and "disabled" in r.error


def test_rows_are_capped(sample_db: Path) -> None:
    small = ReadOnlyDB(sample_db, max_rows=5)
    r = small.run("SELECT * FROM range(100)")
    assert len(r.rows) == 5 and r.truncated
    assert "row limit" in r.to_text()
    small.close()


def test_slow_queries_are_stopped(sample_db: Path) -> None:
    slow = ReadOnlyDB(sample_db, timeout_s=0.5)
    r = slow.run("SELECT count(*) FROM range(1000000000) a, range(1000) b")
    assert r.error and "stopped" in r.error
    # the connection still works afterwards
    assert slow.run("SELECT 1").rows == [(1,)]
    slow.close()


def test_errors_come_back_as_text(db: ReadOnlyDB) -> None:
    r = db.run("SELECT no_such_column FROM sales")
    assert r.error and r.to_text().startswith("ERROR:")
