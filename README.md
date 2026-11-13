# duckdb-sql-agent

Ask questions about Irish house prices in plain English. An agent built on Claude writes
SQL against the **Residential Property Price Register** (807,724 sales since 2010), runs it
through a locked-down read-only database, and answers with the figure, the SQL behind it,
and the caveats that matter.

```text
$ sql-agent ask "What was the median price of a new home in Dublin in 2024, including VAT?"

In 2024 the median filed price of a new home in Dublin was €440,529, based on 3,071
market sales. That price leaves out VAT. Adding VAT at 13.5% gives an estimated median
of about €500,000 including VAT.
  - The figure including VAT is an estimate. I added 13.5% to the filed price, which
    leaves out VAT. ...
  - Only market sales are counted. Sales filed as not full market price, bulk portfolio
    sales and possible duplicates are left out.

SQL:
SELECT COUNT(*) n, MEDIAN(price_eur) med, MEDIAN(price_eur)*1.135 med_vat
FROM market_sales WHERE county='Dublin' AND sale_year=2024 AND is_new
```

(A real answer from the eval run below, not a mock-up.)

## Results

Measured on a 43-question eval: 33 questions with a known answer and 10 the agent should
decline. Claude Opus 5.5 at medium effort, one run, November 2026.

| | |
|---|---|
| Correct, as graded | **36 / 43 (84%)**, 95% CI 70–92% |
| Declined when it should | **10 / 10**, with no answerable question wrongly declined |
| Data rules followed | 7 / 7: provisional months, VAT as an estimate, "last year", fewer-than-5 suppression, non-market and bulk sales |
| Cost | **$0.023 per question** (median; range $0.015–0.035), $0.97 for the whole eval |
| Speed | 5.6 s per question (median; slowest 11.5 s), usually one SQL query |

**What the 7 misses were.** I read all of them. None is a wrong answer:

- **6 are grader strictness.** The grader compares the result of the agent's SQL with the
  result of a reference query. Asked "which county had the most sales", the agent returned
  the top five with Dublin first; asked for a percentage change, it returned the two medians
  and stated the change in the text ("about 112.5%" against 112.53%). Right answers, wrong
  shape for the grader.
- **1 is a wrong reference answer.** "Median price in Salthill" was matched on the address
  text, which also picks up 3 sales in Salthill, Monkstown, Co. Dublin. The agent left
  those out and said so in its caveats. It was right; the reference was wrong.

The grader was left unchanged after the run, so the 84% is the score under the rules agreed
before it ran. The honest reading is that Claude answered all 43 correctly, and that this eval
is now too easy to tell a good agent from a better one: harder questions are the next step.

Every case, with its expected answer, is in [evals/cases.md](evals/cases.md); the per-case
results, SQL and answers are in
[.claude/hillclimb/text_to_sql/baseline/results.jsonl](.claude/hillclimb/text_to_sql/baseline/results.jsonl).

## How it works

```
question ──▶ Claude (system prompt: schema, data rules, answer format)
               │  run_sql tool, up to 8 calls
               ▼
            guard ──▶ DuckDB, read-only ──▶ rows as text ──▶ back to Claude
               │
               ▼
            JSON answer: answerable, answer, sql, caveats
```

- **Data.** `sql-agent build-db` downloads the official `PPR-ALL.zip` and loads it into DuckDB
  in about 3 seconds. The file is Windows-1252, not UTF-8 (the euro sign and Irish fadas
  break otherwise). Every raw field is kept in `sales_raw`; `sales` holds typed values.
  Repeat filings are flagged as possible duplicates, and **portfolio sales** (one total
  filed against every unit) are flagged with the rule from my
  [PPR Map](https://github.com/priyansh-koli/ppr-map) project. The `market_sales` view
  leaves those out, and the agent uses it by default.
- **Guard.** Model-written SQL is never trusted. The database is opened read-only; DuckDB's
  file and network access is switched off and the setting locked, so
  `read_csv('/etc/passwd')`, `ATTACH`, `INSTALL` and `COPY ... TO` fail inside the engine;
  only a single `SELECT` is accepted; results stop at 200 rows and queries at 15 seconds.
  Errors go back to the model as text so it can fix its query.
- **Agent.** A hand-written tool loop (not the SDK's beta runner) so every query, token count
  and stop reason is recorded. The final answer is constrained to a JSON schema. The system
  prompt carries the data rules: medians of fewer than 5 sales are withheld, the latest two
  months are flagged as provisional, VAT is only ever added as a labelled estimate, and any
  question that would identify a person is declined. The prompt is byte-stable, so it is
  cached across turns.
- **Eval.** Programmatic grading, no model judge: the agent's final SQL and a reference
  query run on the same database and their results are compared (extra columns allowed,
  counts exact, other figures within 0.1%); single figures must also appear in the written
  answer. Before the paid run the wiring was checked with an oracle (100%) and a null agent
  (0 of 33 answerable). Results are written as each case finishes and a rerun resumes, and
  a paid run refuses to start until the harness is approved after any change.

## Data quirks the eval turned up

- **Portfolio deals filed as a single sale.** The "most expensive home of 2024" is a
  €387.7M filing, and there is a €29M "second-hand home" in Cork. These pass the bulk rule
  (which needs two or more units at the same price), so averages are skewed. The agent
  gives the median next to any average and says why.
- **Place names repeat.** Salthill is in Galway and in Monkstown, Co. Dublin. Address
  matching is approximate, and the agent says so.
- **Eircodes** are on about 75% of sales since 2022 and almost none before 2021, so
  routing-key questions (`D06`, `H91`) undercount.

## Run it

```bash
git clone https://github.com/priyansh-koli/duckdb-sql-agent && cd duckdb-sql-agent
python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'
cp .env.example .env          # then add your ANTHROPIC_API_KEY
.venv/bin/sql-agent build-db    # downloads the PPR (~19 MB) and builds data/ppr.duckdb
.venv/bin/sql-agent ask "Which county had the lowest median price in 2025?"
.venv/bin/sql-agent serve       # the web page at http://127.0.0.1:8000
```

Each question costs about 2–3 cents of Claude usage on your own key. The tests need no key:

```bash
.venv/bin/pytest              # 49 tests: build, guard, agent loop (scripted), grader
.venv/bin/python evals/run_eval.py --mode oracle   # eval wiring check, no API calls
```

To rerun the eval (about $1): run `evals/run_eval.py --approve-harness` once, then
`evals/run_eval.py`.

## Layout

| Path | What |
|---|---|
| `src/duckdb_sql_agent/build.py` | download, cp1252 parsing, duplicate and bulk flags, DuckDB build |
| `src/duckdb_sql_agent/sqlguard.py` | the read-only, locked-down query runner |
| `src/duckdb_sql_agent/prompt.py` | system prompt, tool definition, answer schema |
| `src/duckdb_sql_agent/agent.py` | the tool loop |
| `src/duckdb_sql_agent/cli.py`, `web.py` | command line and web page |
| `evals/` | cases, grader, runner |
| `tests/` | pytest, with a 17-row sample of the real register (Irish-language and mis-encoded descriptions, a duplicate pair, two bulk groups) |

Stack: Python 3.12, Anthropic Python SDK 2.8 (Claude Opus 5.5), DuckDB 2.0, FastAPI, Typer;
ruff, mypy strict, pytest.

## Data and attribution

Contains Residential Property Price Register data © Property Services Regulatory Authority
([propertypriceregister.ie](https://www.propertypriceregister.ie)), re-used under the PSRA
re-use terms. The data may contain errors and is not a property price index. Answers are
generated by a language model and can be wrong: check the SQL.

The register has no buyer or seller names, and this project adds none.

## Licence

Code: MIT, see [LICENSE](LICENSE). © 2026 Priyansh Koli. The PPR data is under the PSRA's re-use terms above.
