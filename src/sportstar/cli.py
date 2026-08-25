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

from .backfill import run_backfill
from .backtesting.run import run as run_backtest_cmd
from .capture import run_capture
from .db.catalog import League, Sport, Sportsbook, Team
from .db.session import create_db_engine, create_session_factory, database_url, session_scope
from .demo import run_demo
from .health import persist_report, run_checks
from .odds_history import run as run_odds_history
from .seeds import seed_catalog
from .sync import run_sync

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


def cmd_health() -> int:
    """Ejecuta los checks y sincroniza el panel.

    Devuelve 1 si hay CRITICAL, para que sirva como paso de un cron o de CI: un
    problema de datos debe poder romper un pipeline, no solo pintarse en una
    pantalla que nadie mira.
    """
    engine = create_db_engine()
    factory = create_session_factory(engine)
    with session_scope(factory) as session:
        report = run_checks(session)
        created, resolved = persist_report(session, report)
        print(report.render())
        print(f"\n  nuevos: {created}   resueltos: {resolved}")
        healthy = report.is_healthy
    engine.dispose()
    return 0 if healthy else 1


def cmd_serve() -> int:
    """Levanta la API.

    `0.0.0.0` a propósito: el objetivo es abrirla desde el iPhone en la misma
    red, que es la prueba real de la fase mobile. En cuanto salga de localhost
    hará falta autenticación — la API es de solo lectura, pero el histórico de
    decisiones no tiene por qué ser público.
    """
    import uvicorn

    print("API en http://0.0.0.0:8000    documentación en /docs")
    uvicorn.run("sportstar.api:app", host="0.0.0.0", port=8000, log_level="info")
    return 0


def cmd_backfill(args: argparse.Namespace) -> int:
    from datetime import date

    return run_backfill(date.fromisoformat(args.start), date.fromisoformat(args.end))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sportstar", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="aplica migraciones")
    sub.add_parser("seed", help="puebla el catálogo")
    sub.add_parser("status", help="resumen del contenido de la base")
    sub.add_parser("demo", help="ejecuta el pipeline con precios sintéticos")
    sub.add_parser("capture", help="captura fixtures reales de los proveedores")
    sub.add_parser("sync", help="captura un snapshot del mercado (para programar)")
    sub.add_parser("health", help="checks de calidad de datos")
    sub.add_parser(
        "odds-history", help="descarga, repara y audita el histórico de odds (2011-2021)"
    )
    sub.add_parser("serve", help="levanta la API HTTP")
    backtest = sub.add_parser("backtest", help="compara estrategias sobre el histórico (2011-2021)")
    backtest.add_argument(
        "--test",
        action="store_true",
        help="evalúa también el test set. Queda anotado: cada uso lo acerca a ser train.",
    )
    backfill = sub.add_parser("backfill", help="descarga histórico de MLB a data/raw/")
    backfill.add_argument("--start", required=True, help="fecha inicial (YYYY-MM-DD)")
    backfill.add_argument("--end", required=True, help="fecha final (YYYY-MM-DD)")
    args = parser.parse_args(argv)

    if args.command == "backfill":
        return cmd_backfill(args)
    if args.command == "backtest":
        return run_backtest_cmd(use_test_set=args.test)

    commands = {
        "init": cmd_init,
        "seed": cmd_seed,
        "status": cmd_status,
        "demo": run_demo,
        "capture": run_capture,
        "sync": run_sync,
        "health": cmd_health,
        "odds-history": run_odds_history,
        "serve": cmd_serve,
    }
    return commands[args.command]()


if __name__ == "__main__":
    sys.exit(main())
