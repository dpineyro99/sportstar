"""Histórico de MLB normalizado -> resultados utilizables por el modelo.

El punto delicado es `observed_at`: el histórico no dice cuándo supimos cada
resultado, solo cuándo empezó el partido. Se aproxima con la duración típica.

La aproximación es explícita y conservadora —tarde antes que pronto— porque
errar hacia "lo supimos después" nunca produce leakage: como mucho descarta
información que sí teníamos. Errar hacia "lo supimos antes" sí lo produce, y el
leakage no da error, da resultados mejores.
"""

from __future__ import annotations

from datetime import timedelta

from ...data.normalizers.models import NormalizedEvent
from ..elo import GameResult

# Duración típica de un partido de MLB, redondeada hacia arriba. Los partidos con
# entradas extra duran más, y por eso se redondea al alza: la penalización de
# esperar de más es no usar un dato; la de asumir de menos es contaminar el
# backtest.
TYPICAL_GAME_DURATION = timedelta(hours=3, minutes=30)

# Tipos de partido que cuentan para medir fuerza de equipo: temporada regular y
# playoffs. Se excluyen a propósito:
#
#   S  pretemporada  — se juega con prospectos; el resultado no dice nada
#   E  exhibición    — rivales que no son de la liga (en 2024, Diablos Rojos del
#                      México y un filial de ligas menores)
#   A  All-Star      — equipos que no existen ("American League All-Stars")
#
# Medido sobre 2024: 101 de 2.574 partidos son de estos tipos. Incluirlos metía
# siete equipos fantasma en el catálogo del modelo y movía los ratings con
# resultados que no significan nada.
COMPETITIVE_GAME_TYPES = frozenset({"R", "F", "D", "L", "W", "P"})


def to_game_results(events: list[NormalizedEvent]) -> list[GameResult]:
    """Convierte eventos terminados en resultados, ordenados cronológicamente.

    Descarta lo que no se jugó. Un aplazado o un cancelado no es un empate ni una
    derrota: es un partido que no existió, y meterlo en el histórico del modelo
    inventa información.

    Descarta también pretemporada, exhibiciones y All-Star: son partidos reales
    pero no miden lo que el modelo pretende medir.
    """
    results = []
    for event in events:
        if event.status != "final":
            continue
        if event.game_type is not None and event.game_type not in COMPETITIVE_GAME_TYPES:
            continue
        if event.home_score is None or event.away_score is None:
            continue
        if event.provider_home_team_id is None or event.provider_away_team_id is None:
            continue
        results.append(
            GameResult(
                season=(event.official_date or event.start_time.date()).year,
                home_team_id=int(event.provider_home_team_id),
                away_team_id=int(event.provider_away_team_id),
                home_score=event.home_score,
                away_score=event.away_score,
                observed_at=event.start_time + TYPICAL_GAME_DURATION,
            )
        )
    return sorted(results, key=lambda g: g.observed_at)
