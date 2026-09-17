"""Bilanz: jede ausgesprochene Empfehlung wird gespeichert und später ausgewertet.

Nichts wird aussortiert, auch verlorene Wetten bleiben stehen.
Die Datei state/recommendations.json liegt im GitHub-Projekt und überlebt so jeden Tageslauf.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

from fi import config, db, teams
from fi.optimizer import Suggestion
from fi.providers import football_data_uk as fduk
from fi.providers import odds_api

WON, LOST, OPEN, UNAVAILABLE = "gewonnen", "verloren", "offen", "nicht verfügbar"


def load(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def save(path: Path, recs: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(recs, ensure_ascii=False, indent=1), encoding="utf-8")


def _leg_key(leg: dict) -> tuple:
    return (leg["competition"], leg["match_date"], leg["home"], leg["away"], leg["market"], leg["selection"])


def add(recs: list[dict], target: float, suggestion: Suggestion, created_at: str,
        kind: str = "Zielquote") -> bool:
    legs = [{"competition": l.competition, "match_date": l.match_date, "kickoff": l.kickoff,
             "kickoff_local": l.kickoff_local, "home": l.home, "away": l.away, "market": l.market,
             "selection": l.selection, "label": l.label, "price": l.price, "bookmaker": l.bookmaker,
             "p_market": round(l.p_market, 4), "p_model": None if l.p_model is None else round(l.p_model, 4)}
            for l in suggestion.legs]
    keys = sorted(_leg_key(l) for l in legs)
    if any(r["target"] == target and r.get("kind", "Zielquote") == kind
           and sorted(_leg_key(l) for l in r["legs"]) == keys for r in recs):
        return False  # dieselbe Empfehlung gab es schon
    recs.append({"created_at": created_at, "kind": kind, "target": target, "total_odds": round(suggestion.total_odds, 3),
                 "win_probability": round(suggestion.win_probability, 4),
                 "expected_return": round(suggestion.expected_return, 4), "legs": legs, "result": OPEN})
    return True


def leg_won(conn: sqlite3.Connection, leg: dict) -> bool | None:
    row = conn.execute("SELECT home_goals, away_goals FROM matches WHERE competition=? AND match_date=? "
                       "AND home_team=? AND away_team=? AND home_goals IS NOT NULL",
                       (leg["competition"], leg["match_date"], leg["home"], leg["away"])).fetchone()
    if not row:
        return None
    h, a = row["home_goals"], row["away_goals"]
    winner = {"H": h > a, "D": h == a, "A": h < a, "over": h + a > 2, "under": h + a <= 2}
    return winner[leg["selection"]]


def fetch_cup_results(conn: sqlite3.Connection, api, recs: list[dict], now_utc: dt.datetime) -> int:
    """Europapokal-Ergebnisse fehlen bei football-data.co.uk. Abruf nur, wenn eine offene Empfehlung
    darauf wartet (2 Credits je Wettbewerb)."""
    now_uk = odds_api.to_uk_time(now_utc)
    needed = set()
    for rec in recs:
        if rec["result"] != OPEN:
            continue
        for leg in rec["legs"]:
            kickoff = dt.datetime.fromisoformat(leg["kickoff"])
            if leg["competition"] in config.CUPS and now_uk - dt.timedelta(days=3) < kickoff < now_uk - dt.timedelta(hours=3) \
                    and leg_won(conn, leg) is None:
                needed.add(leg["competition"])
    stored = 0
    for code in needed:
        from fi.odds_sync import SOURCE, league_key
        for event in api.scores(league_key(conn, code)):
            if not event.get("completed") or not event.get("scores"):
                continue
            goals = {s.get("name"): s.get("score") for s in event["scores"]}
            try:
                home_goals, away_goals = int(goals[event["home_team"]]), int(goals[event["away_team"]])
            except (KeyError, TypeError, ValueError):
                continue
            kickoff = odds_api.to_uk_time(odds_api.parse_utc(event["commence_time"]))
            home = teams.resolve(conn, SOURCE, event["home_team"], None) or event["home_team"]
            away = teams.resolve(conn, SOURCE, event["away_team"], None) or event["away_team"]
            db.upsert_match(conn, {"competition": code, "season": config.season_code(config.season_start_for(kickoff.date())),
                                   "match_date": kickoff.date().isoformat(), "kick_time": kickoff.strftime("%H:%M"),
                                   "home_team": home, "away_team": away,
                                   "home_goals": home_goals, "away_goals": away_goals}, fduk.SOURCE, db.utc_now())
            stored += 1
        conn.commit()
    return stored


def settle(conn: sqlite3.Connection, recs: list[dict], today: dt.date) -> None:
    for rec in recs:
        if rec["result"] != OPEN:
            continue
        outcomes = [leg_won(conn, leg) for leg in rec["legs"]]
        if any(o is False for o in outcomes):
            rec["result"] = LOST
        elif all(o is True for o in outcomes):
            rec["result"] = WON
        elif max(dt.date.fromisoformat(l["match_date"]) for l in rec["legs"]) < today - dt.timedelta(days=10):
            rec["result"] = UNAVAILABLE


def summary(recs: list[dict]) -> dict:
    done = [r for r in recs if r["result"] in (WON, LOST)]
    won = [r for r in done if r["result"] == WON]
    return {
        "count": len(recs), "settled": len(done), "won": len(won),
        "expected_wins": round(sum(r["win_probability"] for r in done), 2),
        "profit_per_unit": round(sum(r["total_odds"] - 1 for r in won) - (len(done) - len(won)), 2),
    }
