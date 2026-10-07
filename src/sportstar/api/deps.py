"""Dependencias compartidas de la API."""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..db.session import create_db_engine, create_session_factory

# Límite duro de paginación. Sin él, un cliente móvil con mala conexión puede
# pedir el histórico entero y quedarse colgado; y la PWA es el consumidor
# principal, no un script en un servidor.
MAX_LIMIT = 200
DEFAULT_LIMIT = 50


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_db_engine()


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return create_session_factory(get_engine())


def get_session() -> Iterator[Session]:
    """Sesión de solo lectura por petición.

    La API no escribe: las recomendaciones las produce el pipeline, no una
    petición HTTP. Mantener la API de lectura evita que un cliente pueda alterar
    el histórico de decisiones, que debe ser inmutable para poder auditarlo.
    """
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()
