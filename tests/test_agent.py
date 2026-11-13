"""The agent loop against a scripted stand-in for the Messages API (no network)."""

import json
from types import SimpleNamespace as NS
from typing import Any

from ppr_ask.agent import Agent
from ppr_ask.prompt import ANSWER_SCHEMA, RUN_SQL_TOOL
from ppr_ask.sqlguard import ReadOnlyDB


def usage(i: int = 100, o: int = 20, cr: int = 0, cw: int = 0) -> NS:
    return NS(
        input_tokens=i, output_tokens=o, cache_read_input_tokens=cr, cache_creation_input_tokens=cw
    )


def tool_turn(sql: str, id_: str = "t1") -> NS:
    return NS(
        stop_reason="tool_use",
        model="claude-opus-5-5",
        usage=usage(),
        content=[NS(type="tool_use", id=id_, name="run_sql", input={"sql": sql})],
    )


def final_turn(payload: dict[str, Any], stop: str = "end_turn") -> NS:
    return NS(
        stop_reason=stop,
        model="claude-opus-5-5",
        usage=usage(cr=500),
        content=[NS(type="text", text=json.dumps(payload))],
    )


class FakeAPI:
    def __init__(self, replies: list[NS]):
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> NS:
        # keep a snapshot: the agent mutates its messages list between calls
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.replies.pop(0)


ANSWER = {
    "answerable": True,
    "answer": "10 market sales.",
    "sql": "SELECT count(*) FROM market_sales",
    "caveats": [],
}


def test_runs_sql_then_answers(db: ReadOnlyDB) -> None:
    api = FakeAPI([tool_turn("SELECT count(*) FROM market_sales"), final_turn(ANSWER)])
    out = Agent(db, api).ask("How many market sales?")
    assert out.status == "ok"
    assert out.answer == "10 market sales." and out.answerable is True
    assert [(q.sql, q.rows, q.error) for q in out.queries] == [
        ("SELECT count(*) FROM market_sales", 1, None)
    ]
    # the tool result sent back to the model carries the real number
    tool_result = api.calls[1]["messages"][-1]["content"][0]
    assert tool_result["type"] == "tool_result" and "10" in tool_result["content"]
    assert tool_result["is_error"] is False
    assert out.usage.input_tokens == 200 and out.usage.cache_read_input_tokens == 500
    assert [t["role"] for t in out.trace] == [
        "system",
        "user",
        "tool_call",
        "tool_result",
        "assistant",
    ]


def test_request_shape(db: ReadOnlyDB) -> None:
    api = FakeAPI([final_turn(ANSWER)])
    Agent(db, api, model="claude-opus-5-5", effort="medium").ask("q")
    call = api.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["tools"] == [RUN_SQL_TOOL]
    assert call["output_config"] == {
        "effort": "medium",
        "format": {"type": "json_schema", "schema": ANSWER_SCHEMA},
    }
    assert call["fallbacks"] == "default"
    assert call["cache_control"] == {"type": "ephemeral"}
    # API version 2026-10-21 rejects sampling parameters, and Opus 5.5 rejects forced tools
    for absent in ("temperature", "top_p", "top_k", "tool_choice", "thinking"):
        assert absent not in call


def test_sql_errors_go_back_to_the_model(db: ReadOnlyDB) -> None:
    api = FakeAPI([tool_turn("DROP TABLE sales"), tool_turn("SELECT 1", "t2"), final_turn(ANSWER)])
    out = Agent(db, api).ask("q")
    first = api.calls[1]["messages"][-1]["content"][0]
    assert first["is_error"] is True and "rejected" in first["content"]
    assert len(out.queries) == 2 and out.status == "ok"


def test_unanswerable(db: ReadOnlyDB) -> None:
    payload = {
        "answerable": False,
        "answer": "The register has no bedrooms.",
        "sql": None,
        "caveats": [],
    }
    out = Agent(db, FakeAPI([final_turn(payload)])).ask("How many bedrooms?")
    assert out.answerable is False and out.sql is None and out.queries == []


def test_refusal_and_truncation_are_not_answers(db: ReadOnlyDB) -> None:
    refused = NS(stop_reason="refusal", model="claude-opus-5-5", usage=usage(), content=[])
    assert Agent(db, FakeAPI([refused])).ask("q").status == "refused"
    cut = final_turn(ANSWER, stop="max_tokens")
    assert Agent(db, FakeAPI([cut])).ask("q").status == "truncated"


def test_query_limit(db: ReadOnlyDB) -> None:
    api = FakeAPI([tool_turn("SELECT 1", f"t{i}") for i in range(20)])
    out = Agent(db, api, max_tool_calls=3).ask("q")
    assert out.status == "too_many_queries"
    assert len(out.queries) == 3  # calls past the limit are not run


def test_bad_output(db: ReadOnlyDB) -> None:
    junk = NS(
        stop_reason="end_turn", model="m", usage=usage(), content=[NS(type="text", text="not json")]
    )
    out = Agent(db, FakeAPI([junk])).ask("q")
    assert out.status == "bad_output" and out.answer == "not json"
