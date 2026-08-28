"""Forma del lanzador abridor, reconstruible a cualquier instante.

Por qué FIP y no ERA
--------------------
La ERA mide lo que pasó; el FIP mide lo que el lanzador controla. Un abridor con
mala defensa detrás acumula carreras que no son suyas, y la ERA se las apunta.
El FIP solo usa los tres sucesos que no dependen de nadie más —home runs, bases
por bolas y ponches— y por eso **se estabiliza mucho antes**: con 400 bateadores
enfrentados ya dice algo, mientras que la ERA sigue siendo ruido a mitad de
temporada.

Se usa el núcleo del FIP sin la constante de liga::

    fip_core = (13*HR + 3*BB - 2*K) / entradas

La constante solo sirve para que la escala coincida con la de la ERA, y aquí lo
único que importa son **diferencias entre lanzadores**. Menor es mejor.

El problema del tamaño de muestra, y cómo se trata
--------------------------------------------------
Un abridor con una apertura tiene un FIP sin sentido: dos home runs en cinco
entradas lo mandan a un valor que nadie sostiene. Tratar ese número como si fuese
una medición mete ruido puro en el modelo, y el modelo lo aprende.

La corrección es encogerlo hacia la media de la liga en proporción a lo poco que
sabemos::

    encogido = (observado·n + media_liga·k) / (n + k)

con `n` = bateadores enfrentados y `k` = `SHRINKAGE_BF`. Con pocos bateadores el
valor se pega a la media de la liga; con muchos, al observado.

**La media de la liga también es point-in-time.** Se mantiene acumulando las
apariciones ya observadas, no leyendo el total de la temporada — que incluiría
los partidos que se están prediciendo.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...data.normalizers.mlb_pitchers import PitchingAppearance

#: Bateadores enfrentados a los que se le da tanto peso al observado como a la
#: media de liga. Del orden en que el FIP se estabiliza en la literatura.
SHRINKAGE_BF = 400.0

#: Por debajo de esto no se devuelve valoración: ni siquiera encogida significa
#: nada, y un `None` explícito es mejor que un número que nadie debería usar.
MIN_BF_FOR_RATING = 50


@dataclass(slots=True)
class PitcherTotals:
    """Acumulado de un lanzador. Solo lo necesario para el FIP."""

    outs: int = 0
    home_runs: int = 0
    walks: int = 0
    strikeouts: int = 0
    batters_faced: int = 0
    starts: int = 0

    def add(self, appearance: PitchingAppearance) -> None:
        self.outs += appearance.outs
        self.home_runs += appearance.home_runs
        self.walks += appearance.walks
        self.strikeouts += appearance.strikeouts
        self.batters_faced += appearance.batters_faced
        self.starts += int(appearance.is_start)

    @property
    def innings(self) -> float:
        return self.outs / 3.0

    @property
    def fip_core(self) -> float | None:
        """El núcleo del FIP, sin encoger. `None` si no ha lanzado nada."""
        if self.outs == 0:
            return None
        return (13.0 * self.home_runs + 3.0 * self.walks - 2.0 * self.strikeouts) / self.innings


@dataclass
class PitcherForm:
    """Estado de todos los lanzadores, avanzando en el tiempo.

    Se usa igual que `EloModel`: se consulta **antes** de incorporar el resultado
    del día. Quien invierta ese orden obtendrá un modelo que predice partidos que
    ya ha visto, y el síntoma será un backtest excelente.
    """

    shrinkage_bf: float = SHRINKAGE_BF
    min_batters_faced: int = MIN_BF_FOR_RATING
    totals: dict[int, PitcherTotals] = field(default_factory=dict)
    league: PitcherTotals = field(default_factory=PitcherTotals)

    def observe(self, appearance: PitchingAppearance) -> None:
        """Incorpora una aparición. Se llama en orden cronológico."""
        self.totals.setdefault(appearance.pitcher_id, PitcherTotals()).add(appearance)
        self.league.add(appearance)

    def observe_all(self, appearances: list[PitchingAppearance]) -> None:
        for appearance in appearances:
            self.observe(appearance)

    @property
    def league_fip_core(self) -> float | None:
        """Media de liga **hasta ahora**, nunca el total de la temporada."""
        return self.league.fip_core

    def batters_faced(self, pitcher_id: int) -> int:
        totals = self.totals.get(pitcher_id)
        return totals.batters_faced if totals else 0

    def rating(self, pitcher_id: int) -> float | None:
        """FIP encogido hacia la media de liga. Menor es mejor.

        Devuelve `None` si no hay ni base de comparación ni muestra suficiente:
        un lanzador del que no se sabe nada no se puntúa con la media, se deja
        fuera y que el llamante decida.
        """
        league = self.league_fip_core
        if league is None:
            return None
        totals = self.totals.get(pitcher_id)
        if totals is None or totals.batters_faced < self.min_batters_faced:
            return None
        observed = totals.fip_core
        if observed is None:
            return None
        weight = float(totals.batters_faced)
        return (observed * weight + league * self.shrinkage_bf) / (weight + self.shrinkage_bf)

    def advantage(self, home_pitcher_id: int | None, away_pitcher_id: int | None) -> float | None:
        """Ventaja del abridor local, en unidades de FIP. Positivo = mejor local.

        Se invierte el signo porque en FIP **menor es mejor**: si el visitante
        tiene FIP 4,5 y el local 3,5, la ventaja local es +1,0.

        Devuelve `None` si falta cualquiera de los dos: una ventaja calculada
        contra un lanzador desconocido no es una ventaja pequeña, es una ventaja
        que no se sabe.
        """
        if home_pitcher_id is None or away_pitcher_id is None:
            return None
        home = self.rating(home_pitcher_id)
        away = self.rating(away_pitcher_id)
        if home is None or away is None:
            return None
        return away - home
