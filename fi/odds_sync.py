"""Holt kommende Spiele und aktuelle Quoten von The Odds API und speichert sie.

Sparsam mit dem Gratis-Kontingent (500 Credits/Monat):
1. Pro Wettbewerb kostenlos prüfen, ob Spiele anstehen
2. Nur dann Quoten abrufen (2 Credits mit Siegwette + Über/Unter, 1 Credit nur Siegwette)
3. Reicht das Restguthaben rechnerisch nicht bis Monatsende, nur noch die Siegwette laden
"""
from __future__ import annotations

import calendar
import datetime as dt
import json
import sqlite3

from fi import config, db, teams
from fi.providers import football_data_uk as fduk
from fi.providers import odds_api

SOURCE = odds_api.API_NAME


def _setting(conn: sqlite3.Connection, competition: str) -> str | None:
    row = conn.execute("SELECT external_id FROM source_leagues WHERE source=? AND competition=?",
                       (SOURCE, competition)).fetchone()
    return row["external_id"] if row else None


def league_key(conn: sqlite3.Connection, competition: str) -> str:
    return _setting(conn, competition) or config.ODDS_API_SPORT_KEYS[competition]


def set_league_key(conn: sqlite3.Connection, competition: str, key: str) -> None:
    with conn:
        conn.execute("INSERT OR REPLACE INTO source_leagues VALUES (?, ?, ?)", (SOURCE, competition, key))


def bookmakers(conn: sqlite3.Connection) -> list[str]:
    stored = _setting(conn, "_bookmakers")
    return json.loads(stored) if stored else list(config.ODDS_API_PREFERRED_BOOKMAKERS)


def set_bookmakers(conn: sqlite3.Connection, keys: list[str]) -> None:
    set_league_key(conn, "_bookmakers", json.dumps(keys))


def markets_for_budget(remaining: int | None, competitions_today: int, today: dt.date) -> list[str]:
    """Beide Märkte, solange das Restguthaben bei täglichem Abruf bis Monatsende reicht."""
    if remaining is None:
        return list(config.ODDS_API_MARKETS)
    days_left = calendar.monthrange(today.year, today.month)[1] - today.day + 1
    needed_full = competitions_today * len(config.ODDS_API_MARKETS) * days_left
    return list(config.ODDS_API_MARKETS) if remaining >= needed_full else ["h2h"]


def sync(conn: sqlite3.Connection, api, days: int = 2, today: dt.date | None = None) -> dict:
    until = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days)
    fetched_at = db.utc_now()
    summary = {"leagues": {}, "odds_matches": 0, "credits_used": 0, "markets": None}
    books = bookmakers(conn)

    planned = []
    for code in (*config.LEAGUES, *config.CUPS):
        events = api.events(league_key(conn, code), until)  # kostenlos
        summary["leagues"][code] = {"events": len(events), "stored": 0, "unresolved": []}
        if events:
            planned.append(code)

    markets = markets_for_budget(api.remaining, len(planned), today or dt.date.today())
    summary["markets"] = markets

    for code in planned:
        info = summary["leagues"][code]
        is_league = code in config.LEAGUES
        for event in api.odds(league_key(conn, code), until, markets, books):
            home = teams.resolve(conn, SOURCE, event["home_team"], code if is_league else None)
            away = teams.resolve(conn, SOURCE, event["away_team"], code if is_league else None)
            if is_league and (not home or not away):
                info["unresolved"] += [n for n, r in ((event["home_team"], home), (event["away_team"], away))
                                       if not r]
                continue
            kickoff = odds_api.to_uk_time(odds_api.parse_utc(event["commence_time"]))
            match_id = db.upsert_match(conn, {
                "competition": code,
                "season": config.season_code(config.season_start_for(kickoff.date())),
                "match_date": kickoff.date().isoformat(),
                "kick_time": kickoff.strftime("%H:%M"),
                # Europapokal: Teams außerhalb der 8 Ligen behalten ihren Originalnamen
                "home_team": home or event["home_team"],
                "away_team": away or event["away_team"],
            }, fduk.SOURCE, fetched_at)
            info["stored"] += 1
            quotes = odds_api.parse_odds(event)
            if quotes:
                db.upsert_odds(conn, match_id, [(b, "current", m, s, p) for b, m, s, p, _ in quotes], fetched_at)
                conn.executemany("INSERT INTO odds_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                 [(match_id, SOURCE, b, m, s, p, u, fetched_at) for b, m, s, p, u in quotes])
                summary["odds_matches"] += 1
        summary["credits_used"] += api.used_last_call
        conn.commit()
    return summary
