"""CLI de operación.

python -m sportstar.cli init      # aplica migraciones
python -m sportstar.cli seed      # puebla el catálogo (idempotente)
python -m sportstar.cli status    # qué hay en la base
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

from .capture import run_capture
from .db.catalog import League, Sport, Sportsbook, Team
from .db.session import create_db_engine, create_session_factory, database_url, session_scope
from .demo import run_demo
from .seeds import seed_catalog

ROOT = Path(__file__).resolve().parents[2]


def _alembic_config() -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return cfg


def cmd_init() -> int:
    print(f"Aplicando migraciones sobre {database_url()}")
    command.upgrade(_alembic_config(), "head")
    print("OK")
    return 0


def cmd_seed() -> int:
    engine = create_db_engine()
    factory = create_session_factory(engine)
    with session_scope(factory) as session:
        created = seed_catalog(session)
    total = sum(created.values())
    for table, count in created.items():
        print(f"  {table:<14} {count:>4} creados")
    print("sin cambios (seed idempotente)" if total == 0 else f"{total} filas creadas")
    engine.dispose()
    return 0


def cmd_status() -> int:
    engine = create_db_engine()
    factory = create_session_factory(engine)
    with session_scope(factory) as session:
        print(f"database    : {database_url()}")
        print(f"sports      : {session.query(Sport).count()}")
        print(f"leagues     : {session.query(League).count()}")
        print(f"teams       : {session.query(Team).count()}")
        books = session.query(Sportsbook).all()
        refs = [b.key for b in books if b.is_reference]
        execs = [b.key for b in books if b.is_executable]
        print(f"sportsbooks : {len(books)}")
        # La distinción no es cosmética: los de referencia definen la
        # probabilidad justa, los ejecutables el precio que se consigue.
        print(f"  referencia: {', '.join(refs) or '(ninguno)'}")
        print(f"  ejecutables: {', '.join(execs) or '(ninguno)'}")
    engine.dispose()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sportstar", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="aplica migraciones")
    sub.add_parser("seed", help="puebla el catálogo")
    sub.add_parser("status", help="resumen del contenido de la base")
    sub.add_parser("demo", help="ejecuta el pipeline con precios sintéticos")
    sub.add_parser("capture", help="captura fixtures reales de los proveedores")
    args = parser.parse_args(argv)

    commands = {
        "init": cmd_init,
        "seed": cmd_seed,
        "status": cmd_status,
        "demo": run_demo,
        "capture": run_capture,
    }
    return commands[args.command]()


if __name__ == "__main__":
    sys.exit(main())
