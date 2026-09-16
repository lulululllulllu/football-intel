"""Datenbank-Schicht (SQLite).

Grundregel: Jede gespeicherte Information bekommt einen Zeitstempel (fetched_at),
damit Backtests später nur Daten verwenden, die zum damaligen Zeitpunkt bekannt waren.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

from fi import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    id                INTEGER PRIMARY KEY,
    source            TEXT NOT NULL,     -- Datenquelle, z.B. 'football-data.co.uk'
    competition       TEXT NOT NULL,     -- Liga-Code aus config.LEAGUES
    season            TEXT NOT NULL,     -- z.B. '2425'
    match_date        TEXT NOT NULL,     -- YYYY-MM-DD
    kick_time         TEXT,              -- HH:MM (Zeitzone laut Quelle), falls bekannt
    home_team         TEXT NOT NULL,
    away_team         TEXT NOT NULL,
    home_goals        INTEGER,
    away_goals        INTEGER,
    home_shots        INTEGER,
    away_shots        INTEGER,
    home_shots_target INTEGER,
    away_shots_target INTEGER,
    fetched_at        TEXT NOT NULL,
    UNIQUE (source, competition, match_date, home_team, away_team)
);

CREATE TABLE IF NOT EXISTS odds (
    match_id   INTEGER NOT NULL REFERENCES matches(id),
    bookmaker  TEXT NOT NULL,   -- 'bet365', 'pinnacle', 'average', 'maximum'
    timing     TEXT NOT NULL,   -- 'pre' (vor dem Spieltag erfasst) oder 'closing' (Schlussquote)
    market     TEXT NOT NULL,   -- '1x2' oder 'ou25'
    selection  TEXT NOT NULL,   -- 'H','D','A' bzw. 'over','under'
    price      REAL NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (match_id, bookmaker, timing, market, selection)
);

CREATE TABLE IF NOT EXISTS elo_history (
    match_id      INTEGER PRIMARY KEY REFERENCES matches(id),
    home_elo_pre  REAL NOT NULL,   -- Werte VOR dem Spiel: nur diese dürfen Prognosen speisen
    away_elo_pre  REAL NOT NULL,
    expected_home REAL NOT NULL,
    home_elo_post REAL NOT NULL,
    away_elo_post REAL NOT NULL,
    warmup        INTEGER NOT NULL, -- 1 = Einschwingphase, nicht für Backtests verwenden
    model_version TEXT NOT NULL,
    computed_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team_ratings (
    team            TEXT PRIMARY KEY,
    competition     TEXT NOT NULL,
    season          TEXT NOT NULL,
    rating          REAL NOT NULL,
    last_match_date TEXT NOT NULL,
    model_version   TEXT NOT NULL,
    computed_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clubelo_ratings (
    rating_date TEXT NOT NULL,
    club        TEXT NOT NULL,
    country     TEXT,
    level       INTEGER,
    elo         REAL NOT NULL,
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (rating_date, club)
);

-- Für später: gleiche Teams heißen in verschiedenen Quellen unterschiedlich.
CREATE TABLE IF NOT EXISTS team_aliases (
    source      TEXT NOT NULL,
    source_name TEXT NOT NULL,
    canonical   TEXT NOT NULL,
    PRIMARY KEY (source, source_name)
);

CREATE TABLE IF NOT EXISTS predictions (
    match_id      INTEGER NOT NULL REFERENCES matches(id),
    model_version TEXT NOT NULL,
    market        TEXT NOT NULL,    -- '1x2', 'ou25', 'btts'
    selection     TEXT NOT NULL,
    probability   REAL NOT NULL,
    data_cutoff   TEXT NOT NULL,    -- nur Spiele VOR diesem Datum flossen ein
    created_at    TEXT NOT NULL,
    PRIMARY KEY (match_id, model_version, market, selection, created_at)
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    id            INTEGER PRIMARY KEY,
    run_at        TEXT NOT NULL,
    model_version TEXT NOT NULL,
    leagues       TEXT NOT NULL,
    from_season   INTEGER NOT NULL,
    params        TEXT NOT NULL,   -- JSON
    results       TEXT NOT NULL    -- JSON
);

-- Jede abgerufene Quote wird zusätzlich dauerhaft protokolliert (nie überschrieben).
CREATE TABLE IF NOT EXISTS odds_snapshots (
    match_id          INTEGER NOT NULL REFERENCES matches(id),
    source            TEXT NOT NULL,
    bookmaker         TEXT NOT NULL,
    market            TEXT NOT NULL,
    selection         TEXT NOT NULL,
    price             REAL NOT NULL,
    source_updated_at TEXT,
    fetched_at        TEXT NOT NULL
);

-- Zuordnung: Teamname in einer Quelle -> Teamname in unserer Datenbank
CREATE TABLE IF NOT EXISTS team_name_map (
    source      TEXT NOT NULL,
    source_name TEXT NOT NULL,
    canonical   TEXT NOT NULL,
    method      TEXT NOT NULL,   -- 'auto' oder 'manuell'
    score       REAL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (source, source_name)
);

-- Liga-Kennung je Quelle (z.B. Slug bei Odds-API.io)
CREATE TABLE IF NOT EXISTS source_leagues (
    source      TEXT NOT NULL,
    competition TEXT NOT NULL,
    external_id TEXT NOT NULL,
    PRIMARY KEY (source, competition)
);

CREATE TABLE IF NOT EXISTS recommendations (
    id              INTEGER PRIMARY KEY,
    created_at      TEXT NOT NULL,
    target_odds     REAL NOT NULL,
    total_odds      REAL NOT NULL,
    win_probability REAL NOT NULL,
    expected_return REAL NOT NULL,
    legs            TEXT NOT NULL    -- JSON: Spiel, Markt, Auswahl, Quote, Buchmacher, Wahrscheinlichkeiten
);

CREATE TABLE IF NOT EXISTS api_usage (
    api      TEXT NOT NULL,
    day      TEXT NOT NULL,   -- UTC-Datum
    requests INTEGER NOT NULL,
    PRIMARY KEY (api, day)
);
"""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def connect(path: Path | str = config.DB_PATH) -> sqlite3.Connection:
    if isinstance(path, Path):
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def upsert_match(conn: sqlite3.Connection, match: dict, source: str, fetched_at: str) -> int:
    params = {
        "source": source,
        "fetched_at": fetched_at,
        **{k: match.get(k) for k in (
            "competition", "season", "match_date", "kick_time", "home_team", "away_team",
            "home_goals", "away_goals", "home_shots", "away_shots",
            "home_shots_target", "away_shots_target",
        )},
    }
    conn.execute(
        """
        INSERT INTO matches (source, competition, season, match_date, kick_time, home_team,
            away_team, home_goals, away_goals, home_shots, away_shots, home_shots_target,
            away_shots_target, fetched_at)
        VALUES (:source, :competition, :season, :match_date, :kick_time, :home_team,
            :away_team, :home_goals, :away_goals, :home_shots, :away_shots, :home_shots_target,
            :away_shots_target, :fetched_at)
        ON CONFLICT (source, competition, match_date, home_team, away_team) DO UPDATE SET
            -- COALESCE: ein späterer Spielplan-Import darf ein Ergebnis nie wieder löschen
            kick_time = COALESCE(excluded.kick_time, matches.kick_time),
            home_goals = COALESCE(excluded.home_goals, matches.home_goals),
            away_goals = COALESCE(excluded.away_goals, matches.away_goals),
            home_shots = COALESCE(excluded.home_shots, matches.home_shots),
            away_shots = COALESCE(excluded.away_shots, matches.away_shots),
            home_shots_target = COALESCE(excluded.home_shots_target, matches.home_shots_target),
            away_shots_target = COALESCE(excluded.away_shots_target, matches.away_shots_target),
            fetched_at = excluded.fetched_at
        """,
        params,
    )
    row = conn.execute(
        "SELECT id FROM matches WHERE source=? AND competition=? AND match_date=? "
        "AND home_team=? AND away_team=?",
        (source, match["competition"], match["match_date"], match["home_team"], match["away_team"]),
    ).fetchone()
    return row["id"]


def upsert_odds(conn: sqlite3.Connection, match_id: int, odds: list[tuple], fetched_at: str) -> None:
    conn.executemany(
        """
        INSERT INTO odds (match_id, bookmaker, timing, market, selection, price, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (match_id, bookmaker, timing, market, selection) DO UPDATE SET
            price = excluded.price, fetched_at = excluded.fetched_at
        """,
        [(match_id, b, t, m, s, p, fetched_at) for (b, t, m, s, p) in odds],
    )
