"""Write evals/cases.md: every case with the result its gold SQL gives on the current DB."""

from decimal import Decimal
from pathlib import Path

import duckdb
import yaml

ROOT = Path(__file__).resolve().parents[1]


def fmt(v: object) -> str:
    if isinstance(v, float | Decimal):
        return f"{float(v):,.2f}"
    if isinstance(v, int):
        return str(v) if 1900 <= v <= 2100 else f"{v:,}"
    return str(v)


def main() -> None:
    cases = yaml.safe_load((ROOT / "evals" / "cases.yaml").read_text())["cases"]
    con = duckdb.connect(str(ROOT / "data" / "ppr.duckdb"), read_only=True)
    last = con.execute("SELECT last_sale_date FROM dataset_info").fetchone()
    lines = [
        "# Eval cases",
        "",
        f"{len(cases)} questions. For answerable ones, the expected result is what the gold "
        f"SQL returns on the current database (sales up to {last[0] if last else '?'}).",
        "",
        "| # | id | category | question | expected |",
        "|---|---|---|---|---|",
    ]
    for i, c in enumerate(cases, 1):
        if c.get("answerable", True) is False:
            exp = "**decline**"
        else:
            rows = con.execute(c["gold"]).fetchall()
            exp = "; ".join(", ".join(fmt(x) for x in r) for r in rows[:7])
            if c.get("suppress"):
                exp = f"**withhold** (fewer than 5 sales; the median would be {exp})"
            if c.get("must_mention"):
                exp += f" (must mention: {', '.join(c['must_mention'])})"
        q = " ".join(c["question"].split())
        lines.append(f"| {i} | `{c['id']}` | {c['tags'][0]} | {q} | {exp} |")
    (ROOT / "evals" / "cases.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
