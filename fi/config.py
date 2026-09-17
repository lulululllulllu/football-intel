"""Zentrale Konfiguration.

Ligen, Saisons, Pfade und Modellparameter stehen ausschließlich hier,
damit nichts im restlichen Code hart eingetragen ist.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

# --- Pfade -------------------------------------------------------------------
PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data"
RAW_DIR = DATA_DIR / "raw"          # heruntergeladene Original-CSV-Dateien
DB_PATH = DATA_DIR / "football.db"  # SQLite-Datenbank

TIMEZONE = "Europe/Berlin"
HTTP_USER_AGENT = "football-intel-personal/0.1"

# --- Wettbewerbe ---------------------------------------------------------------
# Schlüssel = Liga-Code bei football-data.co.uk.
# api_football_id = Liga-ID bei API-Football (wird mit `python -m fi apicheck` geprüft).
LEAGUES: dict[str, dict] = {
    "E0":  {"name": "Premier League", "country": "ENG", "api_football_id": 39},
    "D1":  {"name": "Bundesliga",     "country": "GER", "api_football_id": 78},
    "SP1": {"name": "La Liga",        "country": "ESP", "api_football_id": 140},
    "I1":  {"name": "Serie A",        "country": "ITA", "api_football_id": 135},
    "F1":  {"name": "Ligue 1",        "country": "FRA", "api_football_id": 61},
    "N1":  {"name": "Eredivisie",     "country": "NED", "api_football_id": 88},
    "P1":  {"name": "Primeira Liga",  "country": "POR", "api_football_id": 94},
    "B1":  {"name": "Pro League",     "country": "BEL", "api_football_id": 144},
}

CUPS: dict[str, dict] = {
    "UCL":  {"name": "UEFA Champions League",  "api_football_id": 2},
    "UEL":  {"name": "UEFA Europa League",     "api_football_id": 3},
    "UECL": {"name": "UEFA Conference League", "api_football_id": 848},
}

FIRST_SEASON = 2015  # = Saison 2015/16


def season_code(start_year: int) -> str:
    """2024 -> '2425' (Format von football-data.co.uk)."""
    return f"{start_year % 100:02d}{(start_year + 1) % 100:02d}"


def season_start_for(date: dt.date) -> int:
    """Startjahr der Saison, zu der ein Datum gehört (Saisonwechsel ab Juli)."""
    return date.year if date.month >= 7 else date.year - 1


def seasons(first: int = FIRST_SEASON, today: dt.date | None = None) -> list[int]:
    today = today or dt.date.today()
    return list(range(first, season_start_for(today) + 1))


# --- Elo-Modell ------------------------------------------------------------------
# Startwerte. Sie werden später per Backtest optimiert, nicht von Hand geraten.
ELO = {
    "initial": 1500.0,        # Startwert, wenn eine Liga zum ersten Mal auftaucht
    "k": 20.0,                # Lerngeschwindigkeit pro Spiel
    "home_advantage": 65.0,   # Heimvorteil in Elo-Punkten
    "season_carryover": 0.80, # Anteil des Abstands zum Liga-Mittel, der eine Sommerpause übersteht
    "promoted_pool": 3,       # Aufsteiger starten mit dem Mittel der N schwächsten Vorjahresteams
}
ELO_MODEL_VERSION = "elo-v1"

# --- Tormodell (Dixon-Coles) --------------------------------------------------------
# Startwerte, werden im Backtest eingestellt.
GOALS = {
    "window_days": 900,       # nur Spiele der letzten ~2,5 Jahre
    "half_life_days": 300.0,  # ein 300 Tage altes Spiel zählt halb so viel wie ein heutiges
    "prior_matches": 15.0,    # so viele "gedachte Spiele" auf Elo-Niveau stabilisieren kleine Stichproben
    "elo_prior_beta": 0.6,    # wie stark Elo-Unterschiede in diese gedachten Spiele eingehen
    "max_goals": 10,
    "iterations": 300,
}
GOALS_MODEL_VERSION = "dc-v2"
LOW_DATA_THRESHOLD = 8.0      # effektive (gewichtete) Spiele, darunter Warnhinweis

# --- API-Football (Gratis-Tarif) -------------------------------------------------------
API_FOOTBALL_BASE_URL = "https://v3.football.api-sports.io"
API_FOOTBALL_DAILY_LIMIT = 100
API_FOOTBALL_RESERVE = 5  # diese Anfragen bleiben immer frei, damit nichts hart ins Limit läuft

# --- The Odds API (the-odds-api.com, Gratis-Tarif: 500 Credits pro Monat) ------------------
# Kosten pro Abruf = Anzahl Märkte x 1 (bis zu 10 Buchmacher zählen wie 1 Region).
# Spielpläne (/events) und die Liste der Wettbewerbe (/sports) sind kostenlos.
ODDS_API_BASE_URL = "https://api.the-odds-api.com/v4"
ODDS_API_PREFERRED_BOOKMAKERS = ["bet365", "betway"]  # werden bei `oddscheck` bevorzugt ausgewählt
ODDS_API_MAX_BOOKMAKERS = 10
ODDS_API_MARKETS = ["h2h", "totals"]
ODDS_API_MONTHLY_CREDITS = 500

# Wettbewerbs-Kennungen. Werden mit `python -m fi oddscheck` kostenlos geprüft.
ODDS_API_SPORT_KEYS = {
    "E0": "soccer_epl",
    "D1": "soccer_germany_bundesliga",
    "SP1": "soccer_spain_la_liga",
    "I1": "soccer_italy_serie_a",
    "F1": "soccer_france_ligue_one",
    "N1": "soccer_netherlands_eredivisie",
    "P1": "soccer_portugal_primeira_liga",
    "B1": "soccer_belgium_first_div",
    "UCL": "soccer_uefa_champs_league",
    "UEL": "soccer_uefa_europa_league",
    "UECL": "soccer_uefa_europa_conference_league",
}
ODDS_API_SEARCH_WORDS = {
    "E0": ["epl", "premier"], "D1": ["germany"], "SP1": ["spain"], "I1": ["italy"], "F1": ["france"],
    "N1": ["netherlands"], "P1": ["portugal"], "B1": ["belgium"],
    "UCL": ["champs", "champions"], "UEL": ["europa"], "UECL": ["conference"],
}

# --- Optimierer ---------------------------------------------------------------------------
MY_BOOKMAKERS = ["skybet"]     # nur Quoten dieser Buchmacher werden empfohlen (änderbar mit `books`)
MIN_BOOKMAKERS_FOR_PROB = 3    # so viele Buchmacher müssen ein Spiel anbieten, damit die Wahrscheinlichkeit zählt
MAX_MODEL_GAP = 0.12           # weicht das Modell stärker vom Markt ab, wird der Tipp ausgeschlossen
MIN_EXPECTED_RETURN = 0.95     # darunter: "Heute keine Wette" (mehr als 5 % Verlust pro Einsatz)
MAX_ODDS_AGE_HOURS = 30        # ältere Quoten gelten als veraltet
TARGET_TOLERANCE = 0.15        # Zielquote 3.00 erlaubt 2.55 bis 3.45

# --- Erkenntnisse aus `python -m fi research` (fast 30.000 Spiele, Lern- und Prüfzeitraum) ---------
# Der Markt überschätzt Außenseiter und unterschätzt Favoriten leicht.
# (Marktwahrscheinlichkeit, tatsächlich eingetreten). Korrigiert werden nur die Ränder, wo der
# Effekt eindeutig ist. Im Mittelbereich (20–65 %) hängt die Abweichung zu stark davon ab, wo
# genau die Wetten im Bereich lagen, dort bleibt alles unverändert.
CALIBRATION_POINTS = [(0.0, 0.0), (0.075, 0.064), (0.15, 0.142), (0.20, 0.20), (0.65, 0.65),
                      (0.75, 0.762), (0.85, 0.871), (1.0, 1.0)]
CALIBRATION_STRENGTH = 0.5     # nur die halbe Korrektur übernehmen: die Bereichsmitten sind Näherungen
MAX_LEG_ODDS = 3.5             # Außenseiter über 3,5 zahlten historisch 57–92 % zurück
LOSS_STREAK_EXCLUDE = 5        # nie auf ein Team nach so vielen Niederlagen in Folge (Rückzahlung 40–71 %)
BOOST_SEASON_END_OVER = 0.015  # Über 2,5 in den letzten 4 Saisonspielen: +1,5 Prozentpunkte (gemessen +2,2 bis +4,4)
BOOST_BALANCED_DRAW = 0.010    # Unentschieden bei Heim 35–45 %: +1,0 Prozentpunkte (gemessen +1,5 bis +1,9)

# --- ClubElo (ligaübergreifende Stärke) -------------------------------------------------
CLUBELO_BASE_URL = "http://api.clubelo.com"
