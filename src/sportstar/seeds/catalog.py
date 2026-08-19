"""Seed del catálogo: deportes, ligas, equipos MLB y sportsbooks.

Idempotente: se puede correr cuantas veces haga falta. Las entidades se
identifican por su `key` natural, no por el id autoincremental.

La clasificación de books no es cosmética. `is_reference` decide quién define la
probabilidad justa contra la que se mide el edge; `is_executable` decide dónde se
puede apostar de verdad. El edge del sistema vive precisamente en la diferencia
entre unos y otros (ARCHITECTURE.md §4.2), así que marcarlos mal invalida todos
los números aguas abajo. Los `is_executable` deben ajustarse a los books a los
que el operador tiene acceso real — decisión D5.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..db.catalog import League, Sport, Sportsbook, Team
from ..db.enums import BookType

SPORTS: list[tuple[str, str]] = [
    ("mlb", "Baseball"),
    ("nba", "Basketball"),
    ("nfl", "American Football"),
    ("nhl", "Ice Hockey"),
    ("ncaab", "College Basketball"),
    ("ncaaf", "College Football"),
]

LEAGUES: list[tuple[str, str, str]] = [
    ("mlb", "mlb", "Major League Baseball"),
    ("nba", "nba", "National Basketball Association"),
    ("nfl", "nfl", "National Football League"),
    ("nhl", "nhl", "National Hockey League"),
    ("ncaab", "ncaab", "NCAA Division I Basketball"),
    ("ncaaf", "ncaaf", "NCAA Division I Football"),
]

# (key, nombre, abreviatura, liga, división)
MLB_TEAMS: list[tuple[str, str, str, str, str]] = [
    ("BAL", "Baltimore Orioles", "BAL", "AL", "East"),
    ("BOS", "Boston Red Sox", "BOS", "AL", "East"),
    ("NYY", "New York Yankees", "NYY", "AL", "East"),
    ("TBR", "Tampa Bay Rays", "TB", "AL", "East"),
    ("TOR", "Toronto Blue Jays", "TOR", "AL", "East"),
    ("CWS", "Chicago White Sox", "CWS", "AL", "Central"),
    ("CLE", "Cleveland Guardians", "CLE", "AL", "Central"),
    ("DET", "Detroit Tigers", "DET", "AL", "Central"),
    ("KCR", "Kansas City Royals", "KC", "AL", "Central"),
    ("MIN", "Minnesota Twins", "MIN", "AL", "Central"),
    ("ATH", "Athletics", "ATH", "AL", "West"),
    ("HOU", "Houston Astros", "HOU", "AL", "West"),
    ("LAA", "Los Angeles Angels", "LAA", "AL", "West"),
    ("SEA", "Seattle Mariners", "SEA", "AL", "West"),
    ("TEX", "Texas Rangers", "TEX", "AL", "West"),
    ("ATL", "Atlanta Braves", "ATL", "NL", "East"),
    ("MIA", "Miami Marlins", "MIA", "NL", "East"),
    ("NYM", "New York Mets", "NYM", "NL", "East"),
    ("PHI", "Philadelphia Phillies", "PHI", "NL", "East"),
    ("WSN", "Washington Nationals", "WSH", "NL", "East"),
    ("CHC", "Chicago Cubs", "CHC", "NL", "Central"),
    ("CIN", "Cincinnati Reds", "CIN", "NL", "Central"),
    ("MIL", "Milwaukee Brewers", "MIL", "NL", "Central"),
    ("PIT", "Pittsburgh Pirates", "PIT", "NL", "Central"),
    ("STL", "St. Louis Cardinals", "STL", "NL", "Central"),
    ("ARI", "Arizona Diamondbacks", "ARI", "NL", "West"),
    ("COL", "Colorado Rockies", "COL", "NL", "West"),
    ("LAD", "Los Angeles Dodgers", "LAD", "NL", "West"),
    ("SDP", "San Diego Padres", "SD", "NL", "West"),
    ("SFG", "San Francisco Giants", "SF", "NL", "West"),
]

# (key, nombre, tipo, is_reference, is_executable)
SPORTSBOOKS: list[tuple[str, str, BookType, bool, bool]] = [
    # Referencia: definen la probabilidad justa. No se apuesta necesariamente aquí.
    ("pinnacle", "Pinnacle", BookType.SHARP, True, False),
    ("circa", "Circa Sports", BookType.SHARP, True, False),
    ("betonline", "BetOnline", BookType.SHARP, True, False),
    # Ejecutables: donde se consigue el precio. D5 debe ajustar esta lista.
    ("draftkings", "DraftKings", BookType.RECREATIONAL, False, True),
    ("fanduel", "FanDuel", BookType.RECREATIONAL, False, True),
    ("betmgm", "BetMGM", BookType.RECREATIONAL, False, True),
    ("caesars", "Caesars", BookType.RECREATIONAL, False, True),
    ("espnbet", "ESPN BET", BookType.RECREATIONAL, False, True),
    ("bet365", "bet365", BookType.RECREATIONAL, False, True),
    ("fanatics", "Fanatics Sportsbook", BookType.RECREATIONAL, False, True),
]


def seed_catalog(session: Session) -> dict[str, int]:
    """Puebla el catálogo. Devuelve cuántas filas se crearon por tabla."""
    created = {"sports": 0, "leagues": 0, "teams": 0, "sportsbooks": 0}

    sports: dict[str, Sport] = {s.key: s for s in session.query(Sport).all()}
    for key, name in SPORTS:
        if key not in sports:
            sport = Sport(key=key, name=name)
            session.add(sport)
            sports[key] = sport
            created["sports"] += 1
    session.flush()

    leagues: dict[str, League] = {lg.key: lg for lg in session.query(League).all()}
    for sport_key, key, name in LEAGUES:
        if key not in leagues:
            league = League(sport_id=sports[sport_key].id, key=key, name=name)
            session.add(league)
            leagues[key] = league
            created["leagues"] += 1
    session.flush()

    mlb = leagues["mlb"]
    existing_teams = {t.key for t in session.query(Team).filter(Team.league_id == mlb.id).all()}
    for key, name, abbrev, conference, division in MLB_TEAMS:
        if key not in existing_teams:
            session.add(
                Team(
                    league_id=mlb.id,
                    key=key,
                    name=name,
                    abbreviation=abbrev,
                    conference=conference,
                    division=division,
                )
            )
            created["teams"] += 1

    existing_books = {b.key for b in session.query(Sportsbook).all()}
    for key, name, book_type, is_reference, is_executable in SPORTSBOOKS:
        if key not in existing_books:
            session.add(
                Sportsbook(
                    key=key,
                    name=name,
                    book_type=book_type,
                    is_reference=is_reference,
                    is_executable=is_executable,
                )
            )
            created["sportsbooks"] += 1

    session.flush()
    return created
