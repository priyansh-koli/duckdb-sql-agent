"""The text-to-SQL agent: Claude writes SQL, runs it through the guard, and answers.

A manual tool loop (rather than the SDK's beta tool runner) so every query, its result,
the token usage and the stop reason are recorded for the eval and the web page.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, cast

from duckdb_sql_agent.prompt import ANSWER_SCHEMA, RUN_SQL_TOOL, SYSTEM_PROMPT
from duckdb_sql_agent.sqlguard import ReadOnlyDB

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "medium"
MAX_TOOL_CALLS = 8
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class MessagesAPI(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def add(self, u: Any) -> None:
        self.input_tokens += getattr(u, "input_tokens", 0) or 0
        self.output_tokens += getattr(u, "output_tokens", 0) or 0
        self.cache_read_input_tokens += getattr(u, "cache_read_input_tokens", 0) or 0
        self.cache_creation_input_tokens += getattr(u, "cache_creation_input_tokens", 0) or 0


@dataclass
class QueryLog:
    sql: str
    rows: int
    error: str | None
    seconds: float


@dataclass
class Answer:
    question: str
    answerable: bool | None = None
    answer: str = ""
    sql: str | None = None
    caveats: list[str] = field(default_factory=list)
    queries: list[QueryLog] = field(default_factory=list)
    model: str | None = None
    stop_reason: str | None = None
    status: str = "ok"  # ok | refused | truncated | too_many_queries | bad_output
    usage: Usage = field(default_factory=Usage)
    seconds: float = 0.0
    trace: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Agent:
    def __init__(
        self,
        db: ReadOnlyDB,
        messages_api: MessagesAPI,
        *,
        model: str | None = None,
        effort: str | None = None,
        max_tool_calls: int = MAX_TOOL_CALLS,
    ):
        self.db = db
        self.api = messages_api
        self.model = model or os.environ.get("SQL_AGENT_MODEL", DEFAULT_MODEL)
        self.effort = effort or os.environ.get("SQL_AGENT_EFFORT", DEFAULT_EFFORT)
        self.max_tool_calls = max_tool_calls

    def _request(self, messages: list[dict[str, Any]]) -> Any:
        return self.api.create(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            tools=[RUN_SQL_TOOL],
            messages=messages,
            cache_control={"type": "ephemeral"},
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": ANSWER_SCHEMA},
            },
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )

    def ask(self, question: str) -> Answer:
        started = time.monotonic()
        out = Answer(question=question)
        out.trace = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ]
        messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
        tool_calls = 0
        while True:
            resp = self._request(messages)
            out.usage.add(resp.usage)
            out.model = resp.model
            out.stop_reason = resp.stop_reason
            if resp.stop_reason == "refusal":
                out.status = "refused"
                break
            if resp.stop_reason == "max_tokens":
                out.status = "truncated"
                break
            if resp.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": resp.content})
                continue
            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            if resp.stop_reason == "tool_use" and tool_uses:
                messages.append({"role": "assistant", "content": resp.content})
                results = []
                for block in tool_uses:
                    tool_calls += 1
                    sql = str((block.input or {}).get("sql", ""))
                    out.trace.append(
                        {
                            "role": "tool_call",
                            "name": block.name,
                            "content": json.dumps({"sql": sql}, indent=2),
                        }
                    )
                    if block.name != "run_sql":
                        text, is_error = f"unknown tool {block.name}", True
                    elif tool_calls > self.max_tool_calls:
                        text, is_error = "query limit reached; answer with what you have", True
                    else:
                        qr = self.db.run(sql)
                        out.queries.append(QueryLog(qr.sql, len(qr.rows), qr.error, qr.seconds))
                        text, is_error = qr.to_text(), qr.error is not None
                    out.trace.append({"role": "tool_result", "content": text})
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": text,
                            "is_error": is_error,
                        }
                    )
                messages.append({"role": "user", "content": results})
                if tool_calls > self.max_tool_calls + 2:
                    out.status = "too_many_queries"
                    break
                continue
            self._parse_final(resp, out)
            break
        out.seconds = round(time.monotonic() - started, 2)
        return out

    @staticmethod
    def _parse_final(resp: Any, out: Answer) -> None:
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        out.trace.append({"role": "assistant", "content": text})
        try:
            data = json.loads(text)
            out.answerable = bool(data["answerable"])
            out.answer = str(data["answer"])
            out.sql = data.get("sql")
            out.caveats = [str(c) for c in data.get("caveats") or []]
        except (json.JSONDecodeError, KeyError, TypeError):
            out.status = "bad_output"
            out.answer = text


def default_agent(db: ReadOnlyDB, **kwargs: Any) -> Agent:
    """An Agent on the real Claude API. Credentials come from the environment (.env)."""
    import anthropic

    client = anthropic.Anthropic(max_retries=4)
    return Agent(db, cast(MessagesAPI, client.beta.messages), **kwargs)
