"""Backtest: Wie gut wären die Prognosen in der Vergangenheit gewesen?

Ablauf je Liga, Woche für Woche:
1. Stichtag = erster Spieltag der Woche
2. Elo-Werte und Tormodell NUR aus Spielen vor dem Stichtag
3. Prognosen für alle Spiele der Woche
4. Erst danach fließen deren Ergebnisse ins Elo ein

Bewertet wird mit zwei Maßen (jeweils: kleiner = besser):
- Log Loss: bestraft besonders, wenn etwas mit hoher Sicherheit falsch vorhergesagt wurde
- Brier Score: mittlerer quadratischer Fehler der Wahrscheinlichkeiten

Vergleichsgrößen:
- Markt vorab: Quoten, die vor dem Spieltag erfasst wurden (realistisch für dich beim Wetten)
- Markt Schluss: Pinnacle-Schlussquoten, der strengste Maßstab überhaupt
- Mischung: Markt vorab + ein Anteil Modell. Wird die Mischung besser als der Markt
  allein, liefert das Modell Information, die in den Quoten noch fehlt.
"""
from __future__ import annotations

import datetime as dt
import math
import sqlite3
import time
from dataclasses import dataclass

from fi import config
from fi.models.elo import EloEngine, EloParams
from fi.models.goals import GoalParams, MatchResult, fit, markets, score_matrix
from fi.predict import BOOKMAKER_PREFERENCE, MARKET_SELECTIONS, implied_probabilities

EVAL_MARKETS = ("1x2", "ou25")
BLEND_WEIGHTS = (0.0, 0.1, 0.2, 0.3, 0.5, 1.0)


@dataclass
class Record:
    competition: str
    match_date: str
    low_data: bool
    model: dict            # market -> {selection: p}
    market_pre: dict       # market -> {selection: p}, kann fehlen
    market_close: dict     # market -> {selection: p}, kann fehlen
    outcome: dict          # market -> gewinnende Auswahl


def outcome_of(home_goals: int, away_goals: int) -> dict[str, str]:
    return {
        "1x2": "H" if home_goals > away_goals else ("D" if home_goals == away_goals else "A"),
        "ou25": "over" if home_goals + away_goals > 2 else "under",
        "btts": "yes" if home_goals > 0 and away_goals > 0 else "no",
    }


def log_loss(probs: dict[str, float], winner: str) -> float:
    return -math.log(max(probs[winner], 1e-12))


def brier(probs: dict[str, float], winner: str) -> float:
    return sum((p - (1.0 if s == winner else 0.0)) ** 2 for s, p in probs.items())


def _load_odds(conn: sqlite3.Connection, competition: str) -> dict:
    odds: dict = {}
    for r in conn.execute(
            "SELECT o.match_id, o.bookmaker, o.timing, o.market, o.selection, o.price FROM odds o "
            "JOIN matches m ON m.id = o.match_id WHERE m.competition = ?", (competition,)):
        key = (r["bookmaker"], r["timing"], r["market"])
        odds.setdefault(r["match_id"], {}).setdefault(key, {})[r["selection"]] = r["price"]
    return odds


def _market_probs(match_odds: dict, timing: str, bookmakers) -> dict:
    result = {}
    for market, selections in MARKET_SELECTIONS.items():
        for bookmaker in bookmakers:
            implied = implied_probabilities(match_odds.get((bookmaker, timing, market), {}), selections)
            if implied:
                result[market] = implied[0]
                break
    return result


def run_league(conn: sqlite3.Connection, competition: str, from_season: int,
               goal_params: GoalParams, elo_params: EloParams) -> tuple[list[Record], int]:
    rows = conn.execute(
        "SELECT id, match_date, home_team, away_team, home_goals, away_goals, season FROM matches "
        "WHERE competition=? AND home_goals IS NOT NULL ORDER BY match_date, COALESCE(kick_time,''), id",
        (competition,)).fetchall()
    if not rows:
        return [], 0
    odds = _load_odds(conn, competition)
    results = [MatchResult(dt.date.fromisoformat(r["match_date"]), r["home_team"], r["away_team"],
                           r["home_goals"], r["away_goals"]) for r in rows]
    start = dt.date(from_season, 7, 1)
    engine = EloEngine(elo_params)
    records, skipped, model, i, n = [], 0, None, 0, len(rows)

    def feed_elo(index: int) -> None:
        r = rows[index]
        engine.process(competition, r["season"], r["home_team"], r["away_team"],
                       r["home_goals"], r["away_goals"])

    while i < n and results[i].match_date < start:
        feed_elo(i)
        i += 1

    while i < n:
        cutoff = results[i].match_date
        week_end = cutoff + dt.timedelta(days=7)
        j = i
        while j < n and results[j].match_date < week_end:
            j += 1
        model = fit(results[:i], cutoff, goal_params, dict(engine.ratings), init=model) or model
        for k in range(i, j):
            m = results[k]
            expected = model.expected_goals(m.home, m.away) if model and model.cutoff == cutoff else None
            if expected is None:
                skipped += 1
                continue
            match_odds = odds.get(rows[k]["id"], {})
            records.append(Record(
                competition=competition,
                match_date=rows[k]["match_date"],
                low_data=min(model.effective_games[m.home], model.effective_games[m.away])
                         < config.LOW_DATA_THRESHOLD,
                model=markets(score_matrix(*expected, model.rho, goal_params.max_goals)),
                market_pre=_market_probs(match_odds, "pre", BOOKMAKER_PREFERENCE),
                market_close=_market_probs(match_odds, "closing", ("pinnacle",)),
                outcome=outcome_of(m.home_goals, m.away_goals),
            ))
        for k in range(i, j):
            feed_elo(k)
        i = j
    return records, skipped


def run(conn: sqlite3.Connection, leagues: list[str], from_season: int,
        goal_params: GoalParams, elo_params: EloParams | None = None, verbose: bool = True):
    elo_params = elo_params or EloParams.from_config()
    all_records, skipped_total = [], 0
    for code in leagues:
        started = time.time()
        records, skipped = run_league(conn, code, from_season, goal_params, elo_params)
        all_records += records
        skipped_total += skipped
        if verbose:
            print(f"  {config.LEAGUES[code]['name']:<15} {len(records):>5} Spiele bewertet, "
                  f"{skipped:>3} übersprungen ({time.time() - started:.0f} s)")
    return all_records, skipped_total


# --- Auswertung ------------------------------------------------------------------------

def compare(records: list[Record], market: str) -> dict | None:
    """Vergleich auf GENAU denselben Spielen: nur Spiele mit Vorab- und Schlussquoten."""
    common = [r for r in records if market in r.market_pre and market in r.market_close]
    if not common:
        return None

    def score(get):
        return (sum(log_loss(get(r), r.outcome[market]) for r in common) / len(common),
                sum(brier(get(r), r.outcome[market]) for r in common) / len(common))

    blends = {}
    for w in BLEND_WEIGHTS:
        blends[w] = score(lambda r, w=w: {s: (1 - w) * r.market_pre[market][s] + w * r.model[market][s]
                                          for s in r.model[market]})[0]
    return {"n": len(common), "model": score(lambda r: r.model[market]),
            "pre": score(lambda r: r.market_pre[market]),
            "close": score(lambda r: r.market_close[market]), "blends": blends}


def calibration(records: list[Record], source: str, market: str, width: float = 0.1):
    buckets: dict[int, list] = {}
    for r in records:
        probs = getattr(r, source).get(market)
        if not probs:
            continue
        for selection, p in probs.items():
            b = min(int(p / width), int(1 / width) - 1)
            buckets.setdefault(b, []).append((p, 1.0 if r.outcome[market] == selection else 0.0))
    return [(b * width, (b + 1) * width, len(v), sum(p for p, _ in v) / len(v),
             sum(hit for _, hit in v) / len(v)) for b, v in sorted(buckets.items())]
