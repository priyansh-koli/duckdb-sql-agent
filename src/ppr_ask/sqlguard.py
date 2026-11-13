"""Run model-written SQL safely.

Defence in layers, so no single check has to be perfect:
1. The database file is opened read-only, so no statement can change it.
2. External access is off and the configuration is locked, so `read_csv('/etc/passwd')`,
   `ATTACH`, `INSTALL` and `COPY ... TO` fail inside DuckDB itself.
3. Only one statement, starting with SELECT or WITH, is accepted.
4. Each query has a time limit and the result is capped at `max_rows`.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

MAX_ROWS = 200
TIMEOUT_S = 15.0
_STARTS_OK = re.compile(r"^\s*(?:--[^\n]*\n\s*)*(select|with|from)\b", re.IGNORECASE)


class QueryRejected(ValueError):
    """The SQL was not run: it is not a single read-only query."""


@dataclass
class QueryResult:
    sql: str
    columns: list[str]
    rows: list[tuple[Any, ...]]
    truncated: bool
    seconds: float
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_text(self, max_rows: int = 50) -> str:
        """Compact text for the model: a header line and one line per row."""
        if self.error:
            return f"ERROR: {self.error}"
        lines = [" | ".join(self.columns)]
        for row in self.rows[:max_rows]:
            lines.append(" | ".join(_fmt(v) for v in row))
        shown = min(len(self.rows), max_rows)
        note = f"({len(self.rows)} rows"
        if self.truncated:
            note += f", stopped at the {MAX_ROWS}-row limit; aggregate or add LIMIT"
        elif shown < len(self.rows):
            note += f", first {shown} shown"
        lines.append(note + ")")
        return "\n".join(lines)


def _fmt(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, Decimal | float):
        return f"{v:.2f}" if isinstance(v, float) else str(v)
    if isinstance(v, date | datetime):
        return v.isoformat()
    return str(v)


def check_sql(sql: str) -> str:
    """Return the statement without a trailing semicolon, or raise QueryRejected."""
    stripped = sql.strip().rstrip(";").strip()
    if not stripped:
        raise QueryRejected("empty query")
    if not _STARTS_OK.match(stripped):
        raise QueryRejected("only a single SELECT (or WITH ... SELECT) query is allowed")
    try:
        statements = duckdb.extract_statements(stripped)
    except duckdb.Error as exc:
        raise QueryRejected(f"could not parse the query: {exc}") from None
    if len(statements) != 1:
        raise QueryRejected("send exactly one statement")
    if statements[0].type != duckdb.StatementType.SELECT:
        raise QueryRejected("only SELECT queries are allowed")
    return stripped


class ReadOnlyDB:
    """A locked-down, read-only DuckDB connection."""

    def __init__(self, path: Path, *, timeout_s: float = TIMEOUT_S, max_rows: int = MAX_ROWS):
        if not path.exists():
            raise FileNotFoundError(f"{path} not found; run `ppr-ask build-db` first")
        self.timeout_s = timeout_s
        self.max_rows = max_rows
        self.con = duckdb.connect(
            str(path),
            read_only=True,
            config={"enable_external_access": False, "threads": 4, "memory_limit": "2GB"},
        )
        self.con.execute("SET lock_configuration = true")
        self._lock = threading.Lock()

    def close(self) -> None:
        self.con.close()

    def run(self, sql: str) -> QueryResult:
        """Run one read-only query. Errors come back in the result, not as exceptions."""
        import time

        started = time.monotonic()
        try:
            clean = check_sql(sql)
        except QueryRejected as exc:
            return QueryResult(sql, [], [], False, 0.0, error=f"rejected: {exc}")
        with self._lock:
            cur = self.con.cursor()
            timer = threading.Timer(self.timeout_s, cur.interrupt)
            timer.start()
            try:
                cur.execute(clean)
                columns = [d[0] for d in cur.description or []]
                rows = cur.fetchmany(self.max_rows + 1)
            except duckdb.InterruptException:
                return QueryResult(
                    clean,
                    [],
                    [],
                    False,
                    time.monotonic() - started,
                    error=f"query took longer than {self.timeout_s:.0f} s and was stopped",
                )
            except duckdb.Error as exc:
                return QueryResult(
                    clean, [], [], False, time.monotonic() - started, error=str(exc).strip()
                )
            finally:
                timer.cancel()
                cur.close()
        truncated = len(rows) > self.max_rows
        return QueryResult(
            clean, columns, rows[: self.max_rows], truncated, time.monotonic() - started
        )
