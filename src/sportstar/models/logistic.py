"""Regresión logística sobre features de equipo.

Elegida a propósito frente a algo más potente. Con ~2.400 filas y cinco features,
un modelo de árboles encontraría estructura que no existe: el resultado de un
partido de béisbol es mayoritariamente ruido, y la capacidad sobrante se gasta en
memorizarlo.

Además, sus coeficientes **son** las razones de la recomendación. Un modelo cuyas
explicaciones hay que reconstruir a posteriori con SHAP es un modelo cuyas
explicaciones nadie audita.

`predict_proba` produce probabilidades calibradas por construcción —es lo que
optimiza la verosimilitud logística—, que es exactamente lo que el sistema
necesita: el stake se calcula a partir de la probabilidad, así que una
probabilidad inflada produce una apuesta inflada.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from ..features.mlb.builder import MlbFeatureRow

MODEL_NAME = "mlb_logistic"
MODEL_VERSION = "v1"

# Regularización L2 moderada. Con cinco features y 2.400 filas el riesgo de
# sobreajuste es bajo pero no nulo, y en un dominio de baja señal conviene que el
# modelo sea tímido: un coeficiente inflado se traduce en un stake inflado.
DEFAULT_C = 1.0

# Partidos mínimos por equipo antes de fiarse de sus features. En abril nadie
# tiene forma reciente ni récord: esas filas no son datos, son ruido con formato
# de dato, y entrenar con ellas enseña al modelo a leer el ruido.
DEFAULT_BURN_IN = 20


@dataclass
class LogisticSportModel:
    """Regresión logística con estandarización, entrenada sobre filas de features."""

    name: str = MODEL_NAME
    version: str = MODEL_VERSION
    feature_names: tuple[str, ...] = ()
    C: float = DEFAULT_C
    trained_at: datetime | None = None
    n_train: int = 0
    _pipeline: Any = field(default=None, repr=False)

    @property
    def is_fitted(self) -> bool:
        return self._pipeline is not None

    def fit(self, rows: list[MlbFeatureRow], feature_names: tuple[str, ...]) -> LogisticSportModel:
        """Entrena. `rows` debe venir de un periodo **anterior** al de evaluación."""
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler

        if not rows:
            raise ValueError("no se puede entrenar sin filas")

        self.feature_names = feature_names
        features = [row.vector(feature_names) for row in rows]
        labels = [row.label for row in rows]

        self._pipeline = Pipeline(
            [
                # Estandarizar no cambia las predicciones pero sí hace los
                # coeficientes comparables entre sí, y esos coeficientes son las
                # razones que se muestran al usuario.
                ("scale", StandardScaler()),
                ("model", LogisticRegression(C=self.C, max_iter=1000)),
            ]
        )
        self._pipeline.fit(features, labels)
        self.trained_at = datetime.now(UTC)
        self.n_train = len(rows)
        return self

    def predict_proba(self, rows: list[MlbFeatureRow]) -> list[float]:
        """Probabilidad de victoria local para cada fila."""
        if not self.is_fitted:
            raise RuntimeError("el modelo no está entrenado")
        features = [row.vector(self.feature_names) for row in rows]
        return [float(p[1]) for p in self._pipeline.predict_proba(features)]

    @property
    def coefficients(self) -> dict[str, float]:
        """Peso de cada feature, en unidades de desviación típica.

        Comparables entre sí gracias a la estandarización. Son la base de las
        explicaciones: no hay que reconstruirlas, ya están.
        """
        if not self.is_fitted:
            return {}
        weights = self._pipeline.named_steps["model"].coef_[0]
        return dict(zip(self.feature_names, (float(w) for w in weights), strict=True))

    @property
    def intercept(self) -> float:
        """Sesgo base. Recoge la ventaja local que las features no explican."""
        if not self.is_fitted:
            return 0.0
        return float(self._pipeline.named_steps["model"].intercept_[0])


def temporal_split(
    rows: list[MlbFeatureRow],
    *,
    train_fraction: float = 0.7,
    burn_in: int = DEFAULT_BURN_IN,
) -> tuple[list[MlbFeatureRow], list[MlbFeatureRow]]:
    """Parte en entrenamiento y test **por tiempo**, nunca al azar.

    Barajar partidos mezcla futuro con pasado: el modelo aprendería de octubre
    para predecir junio y las métricas saldrían mejores de lo que serán en
    producción. Es la forma más común de engañarse en este dominio, y la más
    difícil de detectar después porque el error no deja rastro.

    `burn_in` descarta los primeros partidos de cada equipo, cuando forma
    reciente y récord no son más que ruido con formato de dato.
    """
    if not 0.0 < train_fraction < 1.0:
        raise ValueError(f"train_fraction debe estar en (0, 1), recibido {train_fraction}")

    usable = [r for r in rows if r.min_games_played >= burn_in]
    usable.sort(key=lambda r: r.game.observed_at)
    cut = int(len(usable) * train_fraction)
    return usable[:cut], usable[cut:]
