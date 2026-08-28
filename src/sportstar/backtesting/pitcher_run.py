"""Comando `sportstar backtest-pitchers`: ¿aporta el abridor algo que el mercado no tenga?

La secuencia, y por qué es esta:

1. cruzar el archivo de odds con los abridores de la MLB Stats API, informando de
   cuánto se pierde en el cruce
2. construir filas de entrenamiento sobre **train**, walk-forward
3. ajustar la logística `mercado + correcciones` una sola vez, sobre train
4. congelar los coeficientes y replayear con ellos

El paso 3 se hace sobre train y punto. Reajustar sobre el holdout sería el
equivalente exacto de entrenar en el test set, y el ledger de `splits.py` está
para que eso no pueda pasar en silencio.

El resultado que se busca no es un ROI. Es el **coeficiente de
`starter_advantage`**: si sale ~0 después de ajustar sobre 19.000 partidos, el
mercado ya tenía esa información y no hay nada que rascar por ahí.
"""

from __future__ import annotations

from ..data.providers.mlb_stats_api import MlbStatsApiProvider
from ..odds_history import load as load_odds
from ..pitchers import PitcherHistory
from ..pitchers import load as load_pitchers
from .dataset import HistoricalGame, to_historical_games
from .engine import run_backtest
from .ensemble import MarketPlusCorrections, build_rows, fit
from .pitcher_join import JoinResult, enrich, join_starters, team_id_map
from .run import Comparison, _print_blocked, _table, blocked, compare
from .splits import DEFAULT_TEST, DEFAULT_TRAIN, HoldoutLedger, Split, temporal_split
from .strategies import MarketConsensus

#: Cruce mínimo aceptable. Por debajo de esto el experimento no mide lo que dice
#: medir: estaría comparando modelos sobre submuestras distintas del histórico.
MIN_MATCH_RATE = 0.90


def club_ids(provider: MlbStatsApiProvider | None = None) -> dict[str, int]:
    """`clubName` -> id de equipo, de la MLB Stats API."""
    source = provider or MlbStatsApiProvider()
    payload = source.fetch_teams().payload
    teams = payload.get("teams", []) if isinstance(payload, dict) else []
    return {
        team["clubName"]: int(team["id"])
        for team in teams
        if isinstance(team, dict) and "clubName" in team and "id" in team
    }


def prepare(
    seasons: range = range(2011, 2022),
    *,
    provider: MlbStatsApiProvider | None = None,
) -> tuple[list[HistoricalGame], PitcherHistory, JoinResult]:
    """Carga odds y lanzadores, y los cruza. Falla si el cruce es pobre."""
    games = to_historical_games(load_odds("mlb").games)
    games = [g for g in games if g.season in seasons]
    pitchers = load_pitchers(seasons)

    result = join_starters(games, pitchers.starters, team_id_map(club_ids(provider)))
    if result.match_rate < MIN_MATCH_RATE:
        raise RuntimeError(
            f"el cruce con los abridores solo alcanza el {result.match_rate:.1%}, por "
            f"debajo del {MIN_MATCH_RATE:.0%} exigido. Comparar modelos con este cruce "
            "sería compararlos sobre submuestras distintas del histórico.\n"
            f"{result.summary()}"
        )
    return enrich(games, result), pitchers, result


def run(use_test_set: bool = False, *, ledger: HoldoutLedger | None = None) -> int:
    """Ejecuta el experimento completo."""
    print("cargando odds y lanzadores (la primera vez tarda; después es caché)...")
    games, pitchers, join = prepare()
    print(join.summary())

    split: Split = temporal_split(games, train=DEFAULT_TRAIN, test=DEFAULT_TEST)
    print(
        f"train {split.train_seasons[0]}-{split.train_seasons[-1]} ({len(split.train)})   "
        f"test {split.test_seasons[0]}-{split.test_seasons[-1]} ({len(split.test)})"
    )
    print()

    rows = build_rows(split.train, pitchers.appearances)
    coefficients = fit(rows)
    # El mismo ajuste sin el mercado. Es el diagnóstico que separa "la feature no
    # vale nada" de "el mercado ya la tenía": si aquí sí predice, es buena y el
    # mercado se le adelantó.
    without_market = fit(rows, use=("elo_diff", "starter_advantage"))

    print("=== coeficientes ajustados sobre train ===")
    print(f"  con mercado : {coefficients.explain()}")
    print(f"  sin mercado : {without_market.explain()}")
    print(f"  filas descartadas por features incompletas: {rows.n_skipped}")
    print()

    def strategies() -> list[object]:
        return [
            MarketConsensus(),
            MarketPlusCorrections(coefficients, pitchers.appearances, version="v1-pitchers"),
            MarketPlusCorrections(without_market, pitchers.appearances, version="v1-sin-mercado"),
        ]

    def evaluate(subset: list[HistoricalGame]) -> list[Comparison]:
        out = []
        for strategy in strategies():
            result = run_backtest(subset, strategy)  # type: ignore[arg-type]
            out.append(compare(result) if result.passed_sanity else blocked(result))
        return out

    print("=== TRAIN (en muestra: los coeficientes se ajustaron aquí) ===")
    train_rows = evaluate(split.train)
    print(_table(train_rows))
    _print_blocked(train_rows)
    print()

    if not use_test_set:
        print("holdout NO evaluado (usa --test para hacerlo, y quedará anotado).")
        return 0

    book = ledger or HoldoutLedger()
    label = f"mlb_pitchers_{split.test_seasons[0]}_{split.test_seasons[-1]}"
    uses = book.record(label)
    print(f"=== HOLDOUT — uso nº {uses} de este conjunto ===")
    warning = book.warning(label)
    if warning:
        print(warning)
    test_rows = evaluate(split.test)
    print(_table(test_rows))
    _print_blocked(test_rows)
    return 0
