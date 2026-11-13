"""Command line: build the database, ask a question, or serve the web page."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv

load_dotenv()

app = typer.Typer(add_completion=False, help="Ask questions about Irish house prices.")


def db_path() -> Path:
    return Path(os.environ.get("PPR_ASK_DB", "data/ppr.duckdb"))


@app.command("build-db")
def build_db(
    source: Annotated[
        Path | None, typer.Option(help="A local PPR-ALL.zip or .csv (default: download)")
    ] = None,
) -> None:
    """Download the PPR (or use a local copy) and build the DuckDB database."""
    from ppr_ask.build import build, download

    if source is None:
        source = download(Path("data/PPR-ALL.zip"))
        typer.echo(f"Downloaded {source}")
    stats = build(source, db_path())
    typer.echo(f"Built {db_path()}: {stats}")


@app.command()
def ask(
    question: str,
    show_sql: Annotated[bool, typer.Option(help="Print the SQL used")] = True,
) -> None:
    """Ask one question."""
    from ppr_ask.agent import default_agent
    from ppr_ask.sqlguard import ReadOnlyDB

    db = ReadOnlyDB(db_path())
    result = default_agent(db).ask(question)
    typer.echo(result.answer)
    for c in result.caveats:
        typer.echo(f"  - {c}")
    if show_sql and result.sql:
        typer.echo(f"\nSQL:\n{result.sql}")
    u = result.usage
    typer.echo(
        f"\n[{result.status}; {result.model}; {len(result.queries)} queries; "
        f"{result.seconds}s; {u.input_tokens}+{u.cache_read_input_tokens} cached in, "
        f"{u.output_tokens} out]"
    )


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Serve the question page at http://127.0.0.1:8000."""
    import uvicorn

    uvicorn.run("ppr_ask.web:app", host=host, port=port)


if __name__ == "__main__":
    app()
