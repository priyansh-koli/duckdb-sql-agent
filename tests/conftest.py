from pathlib import Path

import pytest

from ppr_ask.build import build
from ppr_ask.sqlguard import ReadOnlyDB

FIXTURE = Path(__file__).parent / "fixtures" / "ppr_sample.csv"


@pytest.fixture(scope="session")
def sample_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """17 real PPR rows (cp1252) with Irish descriptions, mojibake, a duplicate pair,
    a not-full-market-price sale and two bulk groups."""
    path = tmp_path_factory.mktemp("db") / "sample.duckdb"
    build(FIXTURE, path)
    return path


@pytest.fixture
def db(sample_db: Path) -> ReadOnlyDB:
    d = ReadOnlyDB(sample_db)
    yield d
    d.close()
