"""Resolución de entidades: string de un proveedor -> entidad del catálogo.

Estrategia en cascada, de más fiable a menos:

1. `external_ids` — el proveedor nos dio su ID y ya lo hemos visto. Exacto.
2. `entity_aliases` — alias aprendido o sembrado a mano. Exacto.
3. Nombre normalizado del catálogo. Exacto tras normalizar.
4. Abreviatura. Exacto.
5. Subconjunto de tokens: el nombre del catálogo cabe entero dentro del entrante.
6. Solapamiento de tokens por encima de un umbral alto.

Lo que no supera el umbral **no se adivina**: va a `unmatched_entities` y se
cuenta en el informe del job. Un emparejamiento incorrecto es peor que ninguno,
porque el incorrecto no se ve.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.catalog import EntityAlias, ExternalId, Team
from ..db.enums import EntityType
from ..db.ops import UnmatchedEntity
from .names import name_tokens, normalize_name, token_overlap

# Por debajo de este solapamiento no se empareja. Alto a propósito: "New York
# Yankees" vs "New York Mets" comparten 2 de 4 tokens (0.5) y son equipos
# distintos de la misma ciudad — el error más fácil de cometer y más caro.
#
# Con nombres cortos (los de MLB tienen 2-3 tokens) este umbral casi nunca se
# alcanza sin que la coincidencia exacta lo haya resuelto ya: añadir un token
# sobrante a un nombre de 3 da 0.75. Es útil sobre todo en ligas con nombres
# largos, como las universitarias. El trabajo real con nombres cortos lo hace la
# regla de subconjunto.
MIN_TOKEN_OVERLAP = 0.80


@dataclass(frozen=True, slots=True)
class Resolution:
    """Resultado de intentar resolver un nombre."""

    entity_id: int | None
    confidence: float
    method: str

    @property
    def matched(self) -> bool:
        return self.entity_id is not None


UNRESOLVED = Resolution(None, 0.0, "unmatched")


class TeamResolver:
    """Resuelve nombres de equipo dentro de una liga.

    Cachea el catálogo en memoria al construirse: un job de odds resuelve
    decenas de nombres por ejecución y no tiene sentido volver a la base cada vez.
    Vive lo que dura el job, así que no hay riesgo de servir catálogo obsoleto.
    """

    def __init__(self, session: Session, league_id: int) -> None:
        self._session = session
        self._league_id = league_id

        teams = list(session.scalars(select(Team).where(Team.league_id == league_id)))
        self._by_id = {t.id: t for t in teams}
        self._by_normalized_name = {normalize_name(t.name): t.id for t in teams}
        self._by_key = {normalize_name(t.key): t.id for t in teams}
        self._by_abbreviation = {
            normalize_name(t.abbreviation): t.id for t in teams if t.abbreviation
        }
        self._names = [(t.name, t.id) for t in teams]

    def _external_id_match(self, provider: str, provider_id: str | None) -> int | None:
        if provider_id is None:
            return None
        row = self._session.scalars(
            select(ExternalId).where(
                ExternalId.entity_type == EntityType.TEAM,
                ExternalId.provider == provider,
                ExternalId.provider_id == str(provider_id),
            )
        ).first()
        return row.entity_id if row and row.entity_id in self._by_id else None

    def _alias_match(self, raw_name: str) -> tuple[int, float] | None:
        normalized = normalize_name(raw_name)
        rows = self._session.scalars(
            select(EntityAlias).where(EntityAlias.entity_type == EntityType.TEAM)
        ).all()
        for row in rows:
            if normalize_name(row.alias) == normalized and row.entity_id in self._by_id:
                return row.entity_id, row.confidence
        return None

    def resolve(
        self, raw_name: str, *, provider: str, provider_id: str | None = None
    ) -> Resolution:
        """Resuelve un nombre. Nunca lanza: devuelve `UNRESOLVED` si no puede."""
        if not raw_name or not raw_name.strip():
            return UNRESOLVED

        external = self._external_id_match(provider, provider_id)
        if external is not None:
            return Resolution(external, 1.0, "external_id")

        alias = self._alias_match(raw_name)
        if alias is not None:
            return Resolution(alias[0], alias[1], "alias")

        normalized = normalize_name(raw_name)
        for table, method in (
            (self._by_normalized_name, "name"),
            (self._by_key, "key"),
            (self._by_abbreviation, "abbreviation"),
        ):
            if normalized in table:
                return Resolution(table[normalized], 1.0, method)

        subset = self._subset_match(raw_name)
        if subset is not None:
            return subset

        best_id, best_score = None, 0.0
        for name, team_id in self._names:
            score = token_overlap(raw_name, name)
            if score > best_score:
                best_id, best_score = team_id, score

        if best_id is not None and best_score >= MIN_TOKEN_OVERLAP:
            return Resolution(best_id, best_score, "token_overlap")

        return UNRESOLVED

    def _subset_match(self, raw_name: str) -> Resolution | None:
        """El nombre del catálogo cabe entero dentro del entrante.

        Resuelve el caso habitual de los proveedores que adornan el nombre:
        "New York Yankees Baseball Club" contiene los tres tokens de
        "New York Yankees".

        Dos salvaguardas contra el emparejamiento equivocado:

        - Es subconjunto, no solapamiento. "New York" **no** contiene los tres
          tokens de "New York Yankees", así que no resuelve — y "New York Mets"
          tampoco, porque le falta `yankees`.
        - Si varios equipos encajan, gana el de más tokens y **solo** si es
          estrictamente el más largo. Un empate se deja sin resolver: ante
          ambigüedad, la cola de revisión es preferible a acertar por suerte.
        """
        query = name_tokens(raw_name)
        if not query:
            return None

        matches = [
            (team_id, len(tokens))
            for name, team_id in self._names
            if (tokens := name_tokens(name)) and tokens <= query
        ]
        if not matches:
            return None

        matches.sort(key=lambda m: m[1], reverse=True)
        if len(matches) > 1 and matches[0][1] == matches[1][1]:
            return None

        team_id, matched_tokens = matches[0]
        return Resolution(team_id, matched_tokens / len(query), "token_subset")

    def record_unmatched(
        self, raw_name: str, *, provider: str, context: dict[str, object] | None = None
    ) -> UnmatchedEntity:
        """Encola un nombre sin resolver para revisión manual.

        Incrementa `occurrences` si ya estaba: lo que importa para priorizar la
        revisión es cuántas veces nos ha costado datos, no cuántos strings
        distintos hay.
        """
        now = datetime.now(UTC)
        existing = self._session.scalars(
            select(UnmatchedEntity).where(
                UnmatchedEntity.provider == provider,
                UnmatchedEntity.entity_type == EntityType.TEAM,
                UnmatchedEntity.raw_value == raw_name,
            )
        ).first()
        if existing is not None:
            existing.occurrences += 1
            existing.last_seen_at = now
            return existing

        row = UnmatchedEntity(
            provider=provider,
            entity_type=EntityType.TEAM,
            raw_value=raw_name,
            context=context,
            first_seen_at=now,
            last_seen_at=now,
            occurrences=1,
        )
        self._session.add(row)
        return row

    def learn_alias(self, raw_name: str, team_id: int, *, source: str) -> EntityAlias:
        """Persiste un alias confirmado para que la próxima vez sea exacto.

        Solo debe llamarse con emparejamientos verificados: un alias equivocado
        convierte un error puntual en un error permanente.
        """
        alias = EntityAlias(
            entity_type=EntityType.TEAM,
            entity_id=team_id,
            alias=raw_name,
            source=source,
            confidence=1.0,
        )
        self._session.add(alias)
        return alias
