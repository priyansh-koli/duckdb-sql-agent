"""A small web page over the agent: one question box, the answer, and the SQL behind it."""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import files

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from duckdb_sql_agent.agent import Agent, default_agent
from duckdb_sql_agent.cli import db_path
from duckdb_sql_agent.sqlguard import ReadOnlyDB

app = FastAPI(title="duckdb-sql-agent")


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=500)


@lru_cache(maxsize=1)
def get_agent() -> Agent:
    return default_agent(ReadOnlyDB(db_path()))


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return files("duckdb_sql_agent").joinpath("static/index.html").read_text(encoding="utf-8")


@app.post("/api/ask")
def ask(body: AskIn) -> dict[str, object]:
    result = get_agent().ask(body.question.strip())
    if result.status not in ("ok",):
        raise HTTPException(502, f"The agent could not answer ({result.status}).")
    return {
        "answerable": result.answerable,
        "answer": result.answer,
        "sql": result.sql,
        "caveats": result.caveats,
        "queries": len(result.queries),
        "seconds": result.seconds,
        "model": result.model,
    }
