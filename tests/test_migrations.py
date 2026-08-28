"""Migraciones de Alembic.

El test que más valor aporta aquí es `test_migrations_match_the_models`: detecta
la deriva entre los modelos y la migración, que es el fallo clásico de "funciona
en mi máquina" cuando alguien añade una columna y olvida generar la revisión.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from sportstar.db.models import Base
from sportstar.db.session import create_db_engine

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def alembic_config(tmp_path, monkeypatch) -> Config:
    db = tmp_path / "migrations.db"
    monkeypatch.setenv("SPORTSTAR_DATABASE_URL", f"sqlite:///{db}")
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return cfg


def test_upgrade_creates_every_table(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    engine = create_db_engine(os.environ["SPORTSTAR_DATABASE_URL"])
    tables = set(inspect(engine).get_table_names())
    assert set(Base.metadata.tables) <= tables
    assert "alembic_version" in tables
    engine.dispose()


def test_downgrade_leaves_a_clean_database(alembic_config: Config) -> None:
    # Un downgrade que no limpia convierte cualquier rollback en un incidente
    # manual, que es cuando se toman las peores decisiones.
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")
    engine = create_db_engine(os.environ["SPORTSTAR_DATABASE_URL"])
    assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    engine.dispose()


def test_upgrade_is_repeatable_after_downgrade(alembic_config: Config) -> None:
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
    engine = create_db_engine(os.environ["SPORTSTAR_DATABASE_URL"])
    assert set(Base.metadata.tables) <= set(inspect(engine).get_table_names())
    engine.dispose()


def test_migrations_match_the_models(alembic_config: Config) -> None:
    """No debe quedar ninguna diferencia pendiente de autogenerar."""
    command.upgrade(alembic_config, "head")
    engine = create_db_engine(os.environ["SPORTSTAR_DATABASE_URL"])
    with engine.connect() as connection:
        context = MigrationContext.configure(connection)
        diff = compare_metadata(context, Base.metadata)
    engine.dispose()
    assert diff == [], f"El esquema y los modelos han divergido: {diff}"
