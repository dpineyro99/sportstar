"""`market_consensus_v1`: el primer modelo del sistema es el mercado.

Devuelve el consenso sharp sin vig. Un modelo que copia al mercado.

Parece un chiste y hace tres cosas que ningún modelo estadístico puede hacer al
principio:

1. **Valida el pipeline end-to-end** sin que el resultado dependa de la calidad
   del modelado. Si el ciclo completo no funciona con este modelo, el problema
   está en los datos.
2. **Aísla el edge estructural**: cuánto vale, en unidades, solo tener buenos
   snapshots y comparar books. Es el suelo del sistema.
3. **Fija la vara de aceptación.** Ningún modelo posterior pasa a `is_active` si
   no bate su Brier score. Un ROI mejor con peor calibración es varianza, no
   ventaja.

Con esto, el riesgo R4 del audit (los baselines estadísticos no le ganan al
closing line) deja de ser una amenaza existencial y pasa a ser un criterio
verificable.

**Su edge contra sí mismo es cero por construcción**, y así debe ser: comparado
con el consenso da 0, y todo lo que produzca en el pipeline vendrá de la
diferencia entre el consenso y el mejor precio ejecutable — que es precisamente
el edge estructural.
"""

from __future__ import annotations

from datetime import datetime

from ..odds.consensus import ConsensusResult
from .base import ModelPrediction

MODEL_NAME = "market_consensus"
MODEL_VERSION = "v1"


class MarketConsensusModel:
    """Baseline de mercado. No se entrena, no tiene features, no tiene estado."""

    name = MODEL_NAME
    version = MODEL_VERSION

    def predict(
        self, context: ConsensusResult, as_of: datetime | None = None
    ) -> dict[int, ModelPrediction]:
        """Convierte un consenso en predicciones.

        La incertidumbre sale de la **discrepancia entre books de referencia**.
        Es una medida honesta y gratuita: cuando Pinnacle y Circa difieren, el
        mercado está menos seguro, y esa duda debe propagarse hasta la confianza
        de la recomendación en vez de perderse en el promedio.

        Con un solo book de referencia la dispersión es 0, lo que sobreestima la
        certeza. Por eso el filtro exige un mínimo de books (`filters/gates.py`)
        en vez de confiar en el intervalo.
        """
        moment = as_of or context.as_of
        predictions: dict[int, ModelPrediction] = {}

        for selection_id, probability in context.fair_probabilities.items():
            spread = context.dispersion(selection_id)
            lower = max(1e-9, probability - spread) if spread > 0 else None
            upper = min(1 - 1e-9, probability + spread) if spread > 0 else None
            predictions[selection_id] = ModelPrediction(
                selection_id=selection_id,
                probability=probability,
                lower=lower,
                upper=upper,
                model_name=self.name,
                model_version=self.version,
                as_of=moment,
            )
        return predictions
