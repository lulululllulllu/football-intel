"""Prognosen für kommende Spiele: Modell gegen Markt.

Ablauf je Liga:
1. Elo aktualisieren (liefert den Prior für das Tormodell)
2. Tormodell aus allen Spielen VOR heute schätzen
3. Für jedes kommende Spiel Ergebnis-Matrix und Märkte berechnen
4. Marktwahrscheinlichkeiten aus den Quoten ableiten (Marge herausgerechnet)
5. Prognosen mit Zeitstempel speichern (Grundlage für den späteren Track Record)
"""
from __future__ import annotations

import datetime as dt
import sqlite3

from fi import config, db
from fi.models.elo import compute_all
from fi.models.goals import GoalParams, MatchResult, fit, markets, score_matrix

MODEL_VERSION = f"{config.GOALS_MODEL_VERSION}+{config.ELO_MODEL_VERSION}"

# Reihenfolge = Vorzug. Pinnacle gilt als genauester Buchmacher. 'maximum' ist keine
# echte Quote eines Anbieters und taugt nicht zum Herausrechnen der Marge.
BOOKMAKER_PREFERENCE = ("pinnacle", "average", "bet365")
MARKET_SELECTIONS = {"1x2": ("H", "D", "A"), "ou25": ("over", "under"), "btts": ("yes", "no")}


def implied_probabilities(prices: dict[str, float], selections) -> tuple[dict[str, float], float] | None:
    """Quoten -> Wahrscheinlichkeiten ohne Marge. None, wenn eine Auswahl fehlt."""
    if not all(s in prices for s in selections):
        return None
    inverse = {s: 1 / prices[s] for s in selections}
    total = sum(inverse.values())
    return {s: v / total for s, v in inverse.items()}, total - 1


def market_probabilities(conn: sqlite3.Connection, match_id: int) -> dict:
    """Marktwahrscheinlichkeiten je Markt.

    1. Aktuelle Quoten (Odds-API.io): Wahrscheinlichkeiten je Buchmacher ohne Marge,
       dann gemittelt. Zusätzlich die beste Quote je Auswahl.
    2. Sonst: Vorab-Quoten aus football-data.co.uk.
    """
    result = {}
    for market, selections in MARKET_SELECTIONS.items():
        by_book: dict[str, dict] = {}
        latest = None
        for r in conn.execute("SELECT bookmaker, selection, price, fetched_at FROM odds "
                              "WHERE match_id=? AND timing='current' AND market=?", (match_id, market)):
            by_book.setdefault(r["bookmaker"], {})[r["selection"]] = r["price"]
            latest = max(latest or r["fetched_at"], r["fetched_at"])
        implied = {b: x for b, x in ((b, implied_probabilities(p, selections)) for b, p in by_book.items()) if x}
        if implied:
            result[market] = {
                "bookmaker": "/".join(sorted(implied)),
                "margin": min(x[1] for x in implied.values()),
                "probs": {s: sum(x[0][s] for x in implied.values()) / len(implied) for s in selections},
                "best": {s: max((by_book[b][s], b) for b in implied) for s in selections},
                "fetched_at": latest,
            }
            continue
        for bookmaker in BOOKMAKER_PREFERENCE:
            prices = {r["selection"]: r["price"] for r in conn.execute(
                "SELECT selection, price FROM odds WHERE match_id=? AND bookmaker=? "
                "AND timing='pre' AND market=?", (match_id, bookmaker, market))}
            found = implied_probabilities(prices, selections)
            if found:
                result[market] = {"bookmaker": bookmaker, "margin": found[1], "probs": found[0],
                                  "best": {s: (prices[s], bookmaker) for s in selections},
                                  "fetched_at": None}
                break
    return result


def predict_upcoming(conn: sqlite3.Connection, today: dt.date, days: int,
                     leagues: list[str]) -> list[dict]:
    compute_all(conn)
    elo = {r["team"]: r["rating"] for r in conn.execute("SELECT team, rating FROM team_ratings")}
    params = GoalParams.from_config()
    end = today + dt.timedelta(days=days)
    items = []

    for code in leagues:
        fixtures = conn.execute(
            "SELECT * FROM matches WHERE competition=? AND home_goals IS NULL "
            "AND match_date >= ? AND match_date <= ? ORDER BY match_date, kick_time",
            (code, today.isoformat(), end.isoformat())).fetchall()
        if not fixtures:
            continue
        history = [
            MatchResult(dt.date.fromisoformat(r["match_date"]), r["home_team"], r["away_team"],
                        r["home_goals"], r["away_goals"])
            for r in conn.execute(
                "SELECT match_date, home_team, away_team, home_goals, away_goals FROM matches "
                "WHERE competition=? AND home_goals IS NOT NULL AND match_date < ?",
                (code, today.isoformat()))
        ]
        model = fit(history, today, params, elo)

        for f in fixtures:
            item = {"match": f, "league": config.LEAGUES[code]["name"]}
            expected = model.expected_goals(f["home_team"], f["away_team"]) if model else None
            if expected is None:
                item["error"] = "Keine ausreichenden Daten für mindestens ein Team"
                items.append(item)
                continue
            matrix = score_matrix(*expected, model.rho, params.max_goals)
            item.update({
                "expected_goals": expected,
                "model": markets(matrix),
                "market": market_probabilities(conn, f["id"]),
                "effective_games": (model.effective_games[f["home_team"]],
                                    model.effective_games[f["away_team"]]),
                "cutoff": today.isoformat(),
            })
            items.append(item)
    return items


def store_predictions(conn: sqlite3.Connection, items: list[dict]) -> int:
    created_at = db.utc_now()
    rows = [(i["match"]["id"], MODEL_VERSION, market, selection, prob, i["cutoff"], created_at)
            for i in items if "model" in i
            for market, selections in i["model"].items()
            for selection, prob in selections.items()]
    with conn:
        conn.executemany("INSERT INTO predictions VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    return len(rows)
