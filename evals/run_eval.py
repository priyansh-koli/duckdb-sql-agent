"""Run the agent over evals/cases.yaml, grade each answer, and write results.

Output (one directory per variant, the layout the Claude eval report builder reads):
  .claude/hillclimb/text_to_sql/<variant>/results.jsonl   one row per (case, rep)
  .claude/hillclimb/text_to_sql/<variant>/traces/<id>_rep<k>.json
  .claude/hillclimb/text_to_sql/<variant>/errors.jsonl    attempts that produced nothing to grade

Rows are written as each case finishes, and a rerun skips (case, rep) pairs already in
results.jsonl, so an interrupted run resumes. Paid runs need a one-time
`--approve-harness` after the agent, prompt, guard, grader or cases change.

  python evals/run_eval.py --mode oracle          # wiring check, no API calls (expect 100%)
  python evals/run_eval.py --mode null            # wiring check, no API calls (expect ~0%)
  python evals/run_eval.py --limit 5              # pilot
  python evals/run_eval.py                        # full run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "evals"))

from grade import grade  # noqa: E402

from duckdb_sql_agent.agent import DEFAULT_EFFORT, DEFAULT_MODEL, Agent, default_agent  # noqa: E402
from duckdb_sql_agent.sqlguard import ReadOnlyDB  # noqa: E402

FLOW = ROOT / ".claude" / "hillclimb" / "text_to_sql"
HARNESS_PATHS = [
    "evals/run_eval.py",
    "evals/grade.py",
    "evals/cases.yaml",
    "src/duckdb_sql_agent/agent.py",
    "src/duckdb_sql_agent/prompt.py",
    "src/duckdb_sql_agent/sqlguard.py",
]
STATE = {
    "flow": "text_to_sql",
    "metrics": [
        {"id": "correct", "label": "Correct", "kind": "binary"},
        {"id": "classified", "label": "Answerable?", "kind": "binary"},
        {"id": "mentions", "label": "Caveats", "kind": "binary"},
    ],
    "perf_fields": ["latency_s", "tool_calls", "usage"],
    "harness_paths": HARNESS_PATHS,
}
# USD per million tokens (first-party API, as of 2026-11); cache writes 1.25x, reads 0.1x.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}


def harness_sha() -> str:
    h = hashlib.sha256()
    for rel in HARNESS_PATHS:
        h.update(rel.encode())
        h.update((ROOT / rel).read_bytes())
    return h.hexdigest()


def check_harness(approve: bool) -> None:
    approved = FLOW / "harness_approved.json"
    sha = harness_sha()
    if approve:
        approved.write_text(json.dumps({"sha256": sha, "approved_at": time.time()}))
        print(f"Harness approved ({sha[:12]}).")
        return
    if not approved.exists() or json.loads(approved.read_text())["sha256"] != sha:
        print(
            "The agent, prompt, guard, grader or cases changed since the last approved run.\n"
            "Review the change, then rerun with --approve-harness to spend API credit.",
            file=sys.stderr,
        )
        sys.exit(2)


def cost(model: str | None, usage: dict[str, int]) -> float:
    if model not in PRICES:
        return float("nan")
    pin, pout = PRICES[model]
    return (
        usage["input_tokens"] * pin
        + usage["cache_creation_input_tokens"] * pin * 1.25
        + usage["cache_read_input_tokens"] * pin * 0.1
        + usage["output_tokens"] * pout
    ) / 1e6


class ScriptedAgent:
    """No-API stand-ins for wiring checks: the oracle answers with the gold SQL, the null
    agent with nothing."""

    def __init__(self, mode: str, db: ReadOnlyDB, cases: dict[str, dict[str, Any]]):
        self.mode, self.db, self.cases = mode, db, cases

    def ask(self, case: dict[str, Any]) -> dict[str, Any]:
        base = {
            "answerable": None,
            "answer": "",
            "sql": None,
            "caveats": [],
            "queries": [],
            "model": f"scripted-{self.mode}",
            "status": "ok",
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
            "seconds": 0.0,
            "trace": [{"role": "user", "content": case["question"]}],
        }
        if self.mode == "null" or not case.get("answerable", True):
            return {**base, "answerable": self.mode != "null" and False}
        if case.get("suppress"):
            return {**base, "answerable": True, "answer": "Too few sales to report."}
        r = self.db.run(case["gold"])
        figure = f"{r.rows[0][0]}" if len(r.rows) == 1 and len(r.rows[0]) == 1 else ""
        return {
            **base,
            "answerable": True,
            "answer": f"Result: {figure}",
            "sql": case["gold"],
            "caveats": case.get("must_mention", []),
        }


def load_done(path: Path) -> set[tuple[str, int]]:
    if not path.exists():
        return set()
    return {(r["prompt_id"], r["rep"]) for r in map(json.loads, path.read_text().splitlines())}


def wilson(p: float, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def main() -> None:
    load_dotenv(ROOT / ".env")
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="baseline")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--effort", default=DEFAULT_EFFORT)
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--timeout-s", type=float, default=240.0)
    ap.add_argument("--limit", type=int, default=0, help="only the first N cases")
    ap.add_argument("--cases", default="", help="comma-separated case ids")
    ap.add_argument("--mode", choices=["real", "oracle", "null"], default="real")
    ap.add_argument("--approve-harness", action="store_true")
    args = ap.parse_args()

    if args.variant != "baseline" and not args.variant.startswith("v"):
        sys.exit("variant must be 'baseline' or v1, v2, ...")
    FLOW.mkdir(parents=True, exist_ok=True)
    state_path = FLOW / "_state.json"
    if not state_path.exists():
        state_path.write_text(json.dumps(STATE, indent=2))
    if args.mode == "real":
        check_harness(args.approve_harness)
        if args.approve_harness:
            return

    out_dir = FLOW / (args.variant if args.mode == "real" else f"_{args.mode}")
    (out_dir / "traces").mkdir(parents=True, exist_ok=True)
    results_path, errors_path = out_dir / "results.jsonl", out_dir / "errors.jsonl"
    if args.mode != "real":
        results_path.unlink(missing_ok=True)

    cases = yaml.safe_load((ROOT / "evals" / "cases.yaml").read_text())["cases"]
    if args.cases:
        wanted = set(args.cases.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    if args.limit:
        cases = cases[: args.limit]
    by_id = {c["id"]: c for c in cases}

    db = ReadOnlyDB(ROOT / "data" / "ppr.duckdb")
    agent: Agent | ScriptedAgent
    if args.mode == "real":
        agent = default_agent(db, model=args.model, effort=args.effort)
    else:
        agent = ScriptedAgent(args.mode, db, by_id)

    done = load_done(results_path)
    todo = [(c, k) for c in cases for k in range(args.reps) if (c["id"], k) not in done]
    print(
        f"{len(todo)} attempts to run ({len(done)} already done) on "
        f"{args.model if args.mode == 'real' else args.mode}, effort {args.effort}"
    )

    def run_one(case: dict[str, Any], rep: int) -> dict[str, Any]:
        if isinstance(agent, ScriptedAgent):
            return agent.ask(case)
        out = agent.ask(case["question"]).to_dict()
        out["queries"] = [q["sql"] for q in out["queries"]]
        return out

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {pool.submit(run_one, c, k): (c, k) for c, k in todo}
        for fut in as_completed(futures):
            case, rep = futures[fut]
            try:
                ans = fut.result(timeout=args.timeout_s)
            except TimeoutError:
                _append(errors_path, {"prompt_id": case["id"], "rep": rep, "failure": "timeout"})
                continue
            except Exception as exc:  # API errors after the SDK's retries
                _append(
                    errors_path,
                    {
                        "prompt_id": case["id"],
                        "rep": rep,
                        "failure": "harness_or_api_error",
                        "error": f"{type(exc).__name__}: {exc}"[:500],
                    },
                )
                print(f"  ! {case['id']}: {type(exc).__name__}", file=sys.stderr)
                continue
            if args.mode == "real" and ans["model"] != args.model:
                _append(
                    errors_path,
                    {
                        "prompt_id": case["id"],
                        "rep": rep,
                        "failure": "served_model_mismatch",
                        "model": ans["model"],
                        "usage": ans["usage"],
                    },
                )
                continue
            status = {"truncated": "truncated"}.get(ans["status"], "ok")
            g = (
                grade(case, ans, db)
                if status == "ok"
                else {
                    "grade": {"correct": 0.0, "classified": 0.0, "mentions": 0.0},
                    "explanation": {"correct": ans["status"]},
                }
            )
            if ans["status"] in ("refused", "too_many_queries", "bad_output"):
                g["grade"]["correct"] = 0.0
                g["explanation"]["correct"] = f"{ans['status']}; " + g["explanation"]["correct"]
            trace = ans["trace"]
            (out_dir / "traces" / f"{case['id']}_rep{rep}.json").write_text(
                json.dumps(trace, indent=1, ensure_ascii=False)
            )
            row = {
                "prompt_id": case["id"],
                "rep": rep,
                "prompt": case["question"],
                "tags": case["tags"],
                "status": status,
                "stop_reason": ans["stop_reason"],
                "grade": g["grade"],
                "explanation": g["explanation"],
                "model": ans["model"],
                "usage": ans["usage"],
                "latency_s": ans["seconds"],
                "tool_calls": len(ans["queries"]),
                "meta": {
                    "answerable": ans["answerable"],
                    "answer": ans["answer"],
                    "sql": ans["sql"],
                    "caveats": ans["caveats"],
                    "agent_status": ans["status"],
                    "effort": args.effort,
                },
            }
            _append(results_path, row)
            mark = "✓" if g["grade"]["correct"] else "✗"
            print(f"  {mark} {case['id']:<34} {g['explanation']['correct'][:90]}")
    wall = time.monotonic() - started
    summarize(results_path, errors_path, wall, set(by_id))


def _append(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def summarize(results_path: Path, errors_path: Path, wall: float, ids: set[str]) -> None:
    rows = (
        [r for r in map(json.loads, results_path.read_text().splitlines()) if r["prompt_id"] in ids]
        if results_path.exists()
        else []
    )
    scored = [r for r in rows if r["status"] == "ok"]
    n = len(scored)
    if not n:
        print("No scored rows.")
        return
    p = sum(r["grade"]["correct"] for r in scored) / n
    lo, hi = wilson(p, n)
    print(
        f"\nCorrect: {p:.1%} of {n} (95% CI {lo:.0%}-{hi:.0%}); "
        f"truncated {len(rows) - n}; wall {wall:.0f}s"
    )
    by_tag: dict[str, list[float]] = defaultdict(list)
    for r in scored:
        by_tag[r["tags"][0]].append(r["grade"]["correct"])
    for tag, vals in by_tag.items():
        print(f"  {tag:<11} {sum(vals):.0f}/{len(vals)}")
    decline = [r for r in scored if r["tags"][0] == "decline"]
    others = [r for r in scored if r["tags"][0] != "decline"]
    tp = sum(r["meta"]["answerable"] is False for r in decline)
    fp = sum(r["meta"]["answerable"] is False for r in others)
    print(
        f"  declines: {tp}/{len(decline)} correct; {fp} wrongful declines of "
        f"{len(others)} answerable"
    )
    total_usage: dict[str, int] = defaultdict(int)
    dollars = 0.0
    for r in rows:
        for k, v in r["usage"].items():
            total_usage[k] += v
        dollars += cost(r["model"], r["usage"])
    lat = sorted(r["latency_s"] for r in scored)
    print(f"  tokens: {dict(total_usage)}")
    print(
        f"  cost: ${dollars:.2f} total, ${dollars / len(rows):.3f}/case; "
        f"latency median {lat[len(lat) // 2]:.1f}s, max {lat[-1]:.1f}s"
    )
    if errors_path.exists():
        errs = errors_path.read_text().splitlines()
        print(f"  errors (not scored): {len(errs)} -> {errors_path}")


if __name__ == "__main__":
    main()
