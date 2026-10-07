"""Fixtures compartidas."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from sportstar.db.models import Base
from sportstar.db.session import create_db_engine, create_session_factory


@pytest.fixture
def engine(tmp_path) -> Iterator[Engine]:
    """Base SQLite en disco (no en memoria) para que los PRAGMA se comporten
    igual que en producción: `foreign_keys=ON` y WAL."""
    db = tmp_path / "test.db"
    eng = create_db_engine(f"sqlite:///{db}")
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    factory = create_session_factory(engine)
    with factory() as s:
        yield s
