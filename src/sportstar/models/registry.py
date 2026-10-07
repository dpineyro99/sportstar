"""Registro de modelos.

Cada predicción debe saber qué modelo la generó y con qué features. Sin esto,
reentrenar borra la capacidad de evaluar el histórico: las apuestas de hace tres
meses quedan huérfanas y no se puede saber si las produjo la versión buena o la
que abandonamos.

`market_consensus_v1` se registra aquí como cualquier otro modelo, aunque no se
entrene. Es deliberado: es la vara contra la que se comparan los demás, y para
compararlo necesita existir en la misma tabla con las mismas métricas.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.modeling import ModelVersion


def ensure_model_version(
    session: Session,
    *,
    name: str,
    version: str,
    sport_id: int,
    market_type: str,
    algorithm: str,
    is_active: bool = False,
) -> ModelVersion:
    """Devuelve la versión registrada, creándola si no existía. Idempotente.

    No actualiza una versión existente: una versión de modelo es inmutable por
    definición. Si cambia algo del modelo, cambia la versión — si no, las
    predicciones antiguas quedarían atribuidas a algo que ya no es lo que las
    generó.
    """
    existing = session.scalars(
        select(ModelVersion).where(ModelVersion.name == name, ModelVersion.version == version)
    ).first()
    if existing is not None:
        return existing

    row = ModelVersion(
        name=name,
        version=version,
        sport_id=sport_id,
        market_type=market_type,
        algorithm=algorithm,
        is_active=is_active,
    )
    session.add(row)
    session.flush()
    return row


def active_model(session: Session, *, sport_id: int, market_type: str) -> ModelVersion | None:
    """Modelo activo para un deporte y mercado, si hay alguno."""
    return session.scalars(
        select(ModelVersion).where(
            ModelVersion.sport_id == sport_id,
            ModelVersion.market_type == market_type,
            ModelVersion.is_active,
        )
    ).first()
