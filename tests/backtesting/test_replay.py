"""El replay: que el orden temporal sea imposible de invertir por accidente.

Estos son los tests que importan de toda la fase. Un backtest con leakage no
falla: da resultados mejores. La única defensa es comprobar explícitamente que el
modelo no pudo ver lo que no debía.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sportstar.backtesting.dataset import HistoricalGame, MarketPrices
from sportstar.backtesting.replay import UNEVALUABLE_GATES, replay
from sportstar.backtesting.strategies import Elo, MarketConsensus

from .conftest import make_games


class RecordingStrategy:
    """Anota el orden exacto en que se le pide predecir y se le da a observar."""

    name = "recording"
    version = "v1"

    def __init__(self) -> None:
        self.events: list[tuple[str, date]] = []

    def predict_home(self, game: HistoricalGame) -> float | None:
        self.events.append(("predict", game.game_date.date()))
        return 0.5

    def observe(self, game: HistoricalGame) -> None:
        self.events.append(("observe", game.game_date.date()))


def test_ningun_resultado_del_dia_alimenta_una_prediccion_del_dia() -> None:
    """La garantía central: dentro de un día, primero se predice todo, luego se aprende."""
    strategy = RecordingStrategy()

    replay(make_games(n_days=5, games_per_day=4), strategy)

    seen_days: set[date] = set()
    for kind, day in strategy.events:
        if kind == "predict":
            # Si este día ya se había observado, el modelo estaría prediciendo
            # partidos cuyos resultados hermanos ya incorporó.
            assert day not in seen_days, f"predicción del {day} después de observarlo"
        else:
            seen_days.add(day)


def test_las_predicciones_van_en_orden_cronologico() -> None:
    strategy = RecordingStrategy()

    replay(make_games(n_days=10, games_per_day=3), strategy)

    predicted = [day for kind, day in strategy.events if kind == "predict"]
    assert predicted == sorted(predicted)


def test_el_as_of_es_anterior_a_todo_lo_que_lo_alimento() -> None:
    """El contrato point-in-time, comprobado candidate a candidate."""
    result = replay(make_games(n_days=30), Elo(min_games=2))

    checked = 0
    for candidate in result.candidates:
        if candidate.latest_input_observed_at is None:
            continue
        assert candidate.latest_input_observed_at < candidate.as_of
        checked += 1
    assert checked > 0, "el test no comprobó nada"


def test_dos_lados_por_partido_y_sus_probabilidades_suman_uno() -> None:
    result = replay(make_games(n_days=3, games_per_day=2), MarketConsensus())

    assert len(result.candidates) == 2 * result.games_replayed
    for i in range(0, len(result.candidates), 2):
        home, away = result.candidates[i], result.candidates[i + 1]
        assert home.side == "home" and away.side == "away"
        assert home.model_prob + away.model_prob == 1.0
        assert abs(home.market_fair_prob + away.market_fair_prob - 1.0) < 1e-12


def test_el_ganador_de_un_lado_es_el_perdedor_del_otro() -> None:
    result = replay(make_games(n_days=3, games_per_day=2), MarketConsensus())

    for i in range(0, len(result.candidates), 2):
        assert result.candidates[i].won != result.candidates[i + 1].won


def test_el_consenso_de_mercado_no_recomienda_nada() -> None:
    """Su edge de modelo es 0 por construcción, así que nunca supera el vig."""
    result = replay(make_games(n_days=40), MarketConsensus())

    assert result.candidates
    assert not any(c.is_recommended for c in result.candidates)
    assert all(abs(c.edge) < 1e-9 for c in result.candidates)


def test_el_edge_estructural_es_negativo_con_un_solo_precio() -> None:
    """Con una sola línea no hay mejor precio que buscar: solo se paga el vig."""
    result = replay(make_games(n_days=5), MarketConsensus())

    assert all(c.structural_edge < 0.0 for c in result.candidates)


def test_elo_no_opina_hasta_tener_muestra() -> None:
    result = replay(make_games(n_days=60), Elo(min_games=20))

    assert result.games_skipped_no_prediction > 0
    assert result.games_replayed > 0


def test_los_gates_no_evaluables_quedan_declarados() -> None:
    """Un filtro medido con supuestos tiene que decir cuáles."""
    result = replay(make_games(n_days=3), MarketConsensus())

    assert result.unevaluable_gates == UNEVALUABLE_GATES
    assert "line_freshness" in result.unevaluable_gates


def test_el_beneficio_de_una_apuesta_perdida_es_el_stake() -> None:
    from sportstar.backtesting.replay import Candidate
    from sportstar.core.kelly import SizingMethod, Stake
    from sportstar.filters.gates import FilterResult

    def candidate(*, won: bool, units: float) -> Candidate:
        return Candidate(
            game_date=date(2011, 4, 1),
            season=2011,
            archive_sequence=1,
            side="home",
            home_team="A",
            away_team="B",
            strategy="s",
            strategy_version="v",
            model_prob=0.6,
            market_fair_prob=0.5,
            taken_decimal=2.0,
            closing_decimal=1.9,
            closing_fair_prob=0.52,
            edge=0.1,
            structural_edge=-0.01,
            total_edge=0.09,
            expected_value=0.2,
            filter_result=FilterResult(passed=("min_edge",), failed=()),
            stake=Stake(units, SizingMethod.KELLY_FRACTIONAL, 0.2, units, was_capped=False),
            won=won,
            as_of=datetime(2011, 4, 1, tzinfo=UTC),
            latest_input_observed_at=None,
        )

    assert candidate(won=True, units=2.0).profit_units == 2.0
    assert candidate(won=False, units=2.0).profit_units == -2.0
    # Sin stake no hay beneficio ni pérdida, gane o pierda.
    assert candidate(won=False, units=0.0).profit_units == 0.0


def test_el_clv_de_precio_compara_apertura_con_cierre() -> None:
    home = MarketPrices(open_american=100.0, close_american=-110.0)
    away = MarketPrices(open_american=-120.0, close_american=-110.0)
    game = HistoricalGame(
        season=2011,
        game_date=datetime(2011, 4, 1, tzinfo=UTC),
        home_team_id=0,
        away_team_id=1,
        home_team="A",
        away_team="B",
        home_score=5,
        away_score=2,
        home=home,
        away=away,
    )

    result = replay([game], MarketConsensus())
    home_candidate = result.candidates[0]

    # Se tomó +100 (2.0) y cerró en -110 (1.909): el precio tomado fue mejor.
    assert home_candidate.beat_close
    assert home_candidate.clv > 0.0
