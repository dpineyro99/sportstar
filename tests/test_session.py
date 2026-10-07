"""Gestión de sesiones."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine

from sportstar.db.catalog import Sport
from sportstar.db.session import create_session_factory, session_scope


class TestSessionScope:
    def test_commits_on_success(self, engine: Engine) -> None:
        factory = create_session_factory(engine)
        with session_scope(factory) as session:
            session.add(Sport(key="test", name="Test"))
        with factory() as check:
            assert check.query(Sport).filter_by(key="test").one_or_none() is not None

    def test_rolls_back_on_exception(self, engine: Engine) -> None:
        """Una excepción a mitad de un job no puede dejar la base a medio escribir.

        Un sync de odds que falla en el evento 40 de 80 debe dejar cero filas, no
        cuarenta: el estado parcial es indistinguible de un slate corto y no
        dispara ninguna alarma.
        """
        factory = create_session_factory(engine)
        with pytest.raises(RuntimeError), session_scope(factory) as session:
            session.add(Sport(key="test", name="Test"))
            session.flush()
            raise RuntimeError("el job falló a mitad")

        with factory() as check:
            assert check.query(Sport).filter_by(key="test").one_or_none() is None
