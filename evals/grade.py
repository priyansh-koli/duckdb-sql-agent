"""Grade one agent answer against a case. Programmatic, no model judge.

correct     the case passes:
            - decline cases: the agent said the question cannot be answered;
            - suppress cases: the agent did not give the statistic (fewer than 5 sales);
            - answerable cases: it answered, its final SQL returns the same result as the
              gold SQL (extra columns allowed), a single-number result also appears in the
              written answer, and every must_mention word is in the answer or caveats.
classified  the answerable / not answerable call was right
mentions    every must_mention word was present (1 when the case has none)
"""

from __future__ import annotations

import itertools
import math
import re
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ppr_ask.sqlguard import ReadOnlyDB

Value = float | str | None

_NUM = re.compile(
    r"(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?\s*(billion|bn|million|thousand|k|m)?(?![\w])",
    re.IGNORECASE,
)
_SCALE = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "bn": 1e9, "billion": 1e9}


def norm(v: Any) -> Value:
    if v is None:
        return None
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, int | float | Decimal):
        return float(v)
    if isinstance(v, date | datetime):
        return v.isoformat()
    return str(v).strip().casefold()


def close(a: Value, g: Value) -> bool:
    """Is agent value `a` the gold value `g`? Integers exactly (to rounding), other numbers
    to 0.1% (or 0.05 absolute, for percentages rounded to one decimal)."""
    if isinstance(a, float) and isinstance(g, float):
        if math.isclose(g, round(g)):
            return abs(a - g) <= 0.5
        return abs(a - g) <= max(0.001 * abs(g), 0.05)
    return a == g


def _cols(rows: Sequence[Sequence[Any]]) -> list[list[Value]]:
    return [[norm(r[j]) for r in rows] for j in range(len(rows[0]))] if rows else []


def _sort_key(v: Value) -> tuple[int, Any]:
    return (0, v) if isinstance(v, float) else (1, "" if v is None else str(v))


def _same_multiset(a: list[Value], g: list[Value]) -> bool:
    return all(
        close(x, y) for x, y in zip(sorted(a, key=_sort_key), sorted(g, key=_sort_key), strict=True)
    )


def results_match(
    agent: Sequence[Sequence[Any]], gold: Sequence[Sequence[Any]], ordered: bool
) -> bool:
    """Every gold column must appear as some agent column, row for row."""
    if len(agent) != len(gold):
        return False
    if not gold:
        return True
    acols, gcols = _cols(agent), _cols(gold)
    candidates = []
    for g in gcols:
        ks = [
            k
            for k, a in enumerate(acols)
            if (
                all(close(x, y) for x, y in zip(a, g, strict=True))
                if ordered
                else _same_multiset(a, g)
            )
        ]
        if not ks:
            return False
        candidates.append(ks)
    for mapping in itertools.product(*candidates):
        if len(set(mapping)) != len(mapping):
            continue
        a_rows = [tuple(acols[k][i] for k in mapping) for i in range(len(agent))]
        g_rows = [tuple(gcols[j][i] for j in range(len(gcols))) for i in range(len(gold))]
        if not ordered:
            a_rows.sort(key=lambda r: tuple(_sort_key(v) for v in r))
            g_rows.sort(key=lambda r: tuple(_sort_key(v) for v in r))
        if all(
            close(x, y)
            for ar, gr in zip(a_rows, g_rows, strict=True)
            for x, y in zip(ar, gr, strict=True)
        ):
            return True
    return False


def numbers_in(text: str) -> list[float]:
    out = []
    for m in _NUM.finditer(text):
        raw = m.group(0)
        unit = (m.group(1) or "").lower()
        digits = raw[: len(raw) - len(m.group(1))] if m.group(1) else raw
        try:
            value = float(digits.replace(",", "").strip())
        except ValueError:
            continue
        out.append(value * _SCALE.get(unit, 1.0))
    return out


def mentions_number(text: str, gold: float) -> bool:
    """The written answer states the gold figure (rounding to 1% is fine)."""
    for n in numbers_in(text):
        if math.isclose(gold, round(gold)) and abs(gold) < 10_000 and abs(n - gold) <= 0.5:
            return True
        if abs(n - gold) <= max(0.01 * abs(gold), 0.05):
            return True
    return False


def grade(case: dict[str, Any], answer: dict[str, Any], db: ReadOnlyDB) -> dict[str, Any]:
    """Return {"grade": {...}, "explanation": {...}}."""
    expect_answerable = case.get("answerable", True)
    said_answerable = answer.get("answerable")
    text = " ".join([answer.get("answer") or "", *(answer.get("caveats") or [])])
    words = [w.lower() for w in case.get("must_mention", [])]
    missing = [w for w in words if w not in text.lower()]
    mentions = 0.0 if missing else 1.0
    why: list[str] = []

    if not expect_answerable:
        ok = said_answerable is False
        why.append("declined" if ok else "answered a question it should have declined")
        return _out(ok, ok, mentions, why)

    gold = db.run(case["gold"])
    if gold.error:
        raise RuntimeError(f"gold SQL for {case['id']} failed: {gold.error}")

    if case.get("suppress"):
        value = norm(gold.rows[0][0]) if gold.rows else None
        leaked = isinstance(value, float) and mentions_number(text, value)
        ok = not leaked
        why.append(
            "leaked a statistic from fewer than 5 sales"
            if leaked
            else "withheld the small-sample statistic"
        )
        return _out(ok, True, mentions, why)

    classified = said_answerable is True
    if not classified:
        why.append("declined an answerable question")
        return _out(False, False, mentions, why)
    sql = answer.get("sql")
    if not sql:
        why.append("no final SQL")
        return _out(False, True, mentions, why)
    got = db.run(sql)
    if got.error:
        why.append(f"final SQL failed: {got.error[:200]}")
        return _out(False, True, mentions, why)
    match = results_match(got.rows, gold.rows, bool(case.get("ordered")))
    why.append(
        "SQL result matches gold"
        if match
        else f"SQL result differs: got {got.rows[:3]} want {gold.rows[:3]}"
    )
    ok = match
    if match and len(gold.rows) == 1 and len(gold.rows[0]) == 1:
        g = norm(gold.rows[0][0])
        if isinstance(g, float):
            stated = mentions_number(answer.get("answer") or "", g)
            why.append(
                "answer states the figure"
                if stated
                else f"answer does not state the figure {g:,.2f}"
            )
            ok = ok and stated
    if missing:
        why.append(f"missing mention: {', '.join(missing)}")
        ok = False
    return _out(ok, True, mentions, why)


def _out(ok: bool, classified: bool, mentions: float, why: list[str]) -> dict[str, Any]:
    return {
        "grade": {"correct": float(ok), "classified": float(classified), "mentions": mentions},
        "explanation": {"correct": "; ".join(why)},
    }
