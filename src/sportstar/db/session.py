"""Motor y sesiones.

SQLite en modo WAL (decisión D3). El esquema se mantiene compatible con Postgres
vía SQLAlchemy para que la migración no exija reescribir nada cuando la
concurrencia de los workers lo pida.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_DATABASE_URL = "sqlite:///sportstar.db"


def database_url() -> str:
    return os.environ.get("SPORTSTAR_DATABASE_URL", DEFAULT_DATABASE_URL)


def create_db_engine(url: str | None = None, **kwargs: Any) -> Engine:
    """Crea el engine. En SQLite activa WAL y foreign keys.

    SQLite trae las foreign keys **desactivadas** por defecto, así que sin este
    PRAGMA las FKs del esquema serían decorativas y las inconsistencias
    referenciales aparecerían meses después.
    """
    resolved = url or database_url()
    engine = create_engine(resolved, **kwargs)

    if resolved.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Sesión transaccional: commit al salir, rollback ante excepción."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
