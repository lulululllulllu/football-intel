"""Historische Untersuchung: Wo hat sich der Markt in der Vergangenheit systematisch geirrt?

Grundlage: alle gespeicherten Ligaspiele mit Ergebnis und Quoten VOR dem Spiel.
Für jede mögliche Wette (Heim, Unentschieden, Auswärts, Über 2,5, Unter 2,5) gilt:
- Marktwahrscheinlichkeit = Quoten ohne Marge (Durchschnitt, sonst Pinnacle, sonst Bet365)
- Rückzahlung = was 1 € Einsatz im Schnitt zurückgebracht hätte

Schutz vor Scheinergebnissen:
- Faktoren werden auf den Saisons bis SPLIT_SEASON gesucht ("Lernzeitraum") und auf den
  späteren Saisons geprüft ("Prüfzeitraum"). Nur was in beiden Zeiträumen in dieselbe
  Richtung zeigt, gilt als bestätigt.
- Jede Abweichung wird mit ihrer Zufallsschwankung verglichen (z-Wert). Bei vielen Tests
  entstehen einzelne auffällige Werte auch rein zufällig.
- Alle Merkmale (Serien, Ruhetage, Aufsteiger ...) nutzen nur Spiele VOR dem jeweiligen Spiel.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field

from fi import config

SPLIT_SEASON = "2122"  # bis einschließlich 2021/22 lernen, danach prüfen
SELECTIONS = {"H": ("1x2", "Heimsieg"), "D": ("1x2", "Unentschieden"), "A": ("1x2", "Auswärtssieg"),
              "over": ("ou25", "Über 2,5"), "under": ("ou25", "Unter 2,5")}
ODDS_BUCKETS = [(1.0, 1.5), (1.5, 2.0), (2.0, 2.5), (2.5, 3.5), (3.5, 5.0), (5.0, 8.0), (8.0, 1000.0)]
CONSENSUS_PREFERENCE = ("average", "pinnacle", "bet365")


@dataclass
class Bet:
    selection: str
    p_market: float
    price_single: float | None  # Bet365 vor dem Spiel: steht für einen einzelnen Freizeit-Buchmacher
    price_best: float | None    # höchste Quote aller erfassten Buchmacher
    won: bool


@dataclass
class MatchRow:
    competition: str
    season: str
    match_date: str
    home: str
    away: str
    home_goals: int
    away_goals: int
    home_sot: int | None
    away_sot: int | None
    bets: dict = field(default_factory=dict)   # selection -> Bet
    features: dict = field(default_factory=dict)

    @property
    def period(self) -> str:
        return "lernen" if self.season <= SPLIT_SEASON else "prüfen"


def _won(selection: str, h: int, a: int) -> bool:
    return {"H": h > a, "D": h == a, "A": h < a, "over": h + a > 2, "under": h + a <= 2}[selection]


def load(conn: sqlite3.Connection) -> list[MatchRow]:
    odds: dict = defaultdict(dict)
    for r in conn.execute("SELECT match_id, bookmaker, market, selection, price FROM odds WHERE timing='pre'"):
        odds[r["match_id"]].setdefault((r["bookmaker"], r["market"]), {})[r["selection"]] = r["price"]

    rows = []
    for m in conn.execute(
            "SELECT * FROM matches WHERE home_goals IS NOT NULL AND source='football-data.co.uk' "
            "ORDER BY match_date, COALESCE(kick_time, ''), id"):
        if m["competition"] not in config.LEAGUES:
            continue
        row = MatchRow(m["competition"], m["season"], m["match_date"], m["home_team"], m["away_team"],
                       m["home_goals"], m["away_goals"], m["home_shots_target"], m["away_shots_target"])
        match_odds = odds.get(m["id"], {})
        for market, selections in (("1x2", ("H", "D", "A")), ("ou25", ("over", "under"))):
            consensus = None
            for bookmaker in CONSENSUS_PREFERENCE:
                prices = match_odds.get((bookmaker, market), {})
                if all(s in prices for s in selections):
                    inverse = {s: 1 / prices[s] for s in selections}
                    total = sum(inverse.values())
                    consensus = {s: v / total for s, v in inverse.items()}
                    break
            if not consensus:
                continue
            for s in selections:
                single = match_odds.get(("bet365", market), {}).get(s)
                best = match_odds.get(("maximum", market), {}).get(s)
                row.bets[s] = Bet(s, consensus[s], single, best, _won(s, m["home_goals"], m["away_goals"]))
        rows.append(row)
    return rows


# --- Merkmale ohne Lookahead -------------------------------------------------------------

def add_features(rows: list[MatchRow]) -> None:
    history: dict = defaultdict(list)          # (Liga, Team) -> frühere Spiele
    season_teams: dict = defaultdict(set)      # (Liga, Saison) -> Teams
    season_count: dict = defaultdict(int)      # (Liga, Saison, Team) -> Spiele insgesamt (Spielplan)
    for r in rows:
        season_teams[(r.competition, r.season)].add(r.home)
        season_teams[(r.competition, r.season)].add(r.away)
        season_count[(r.competition, r.season, r.home)] += 1
        season_count[(r.competition, r.season, r.away)] += 1
    seasons_by_league: dict = defaultdict(list)
    for league, season in sorted(season_teams):
        seasons_by_league[league].append(season)

    played: dict = defaultdict(int)
    for r in rows:
        date = dt.date.fromisoformat(r.match_date)
        previous_seasons = [s for s in seasons_by_league[r.competition] if s < r.season]
        prev_teams = season_teams[(r.competition, previous_seasons[-1])] if previous_seasons else None
        for side, team, is_home in (("home", r.home, True), ("away", r.away, False)):
            past = history[(r.competition, team)]
            games_this_season = played[(r.competition, r.season, team)]
            last5 = past[-5:]
            last6 = past[-6:]
            rest = (date - past[-1]["date"]).days if past else None
            sot = sum(g["sot"] for g in last6 if g["sot"] is not None)
            goals = sum(g["gf"] for g in last6)
            r.features[side] = {
                "rest_days": rest if rest is not None and rest <= 20 else None,
                "win_streak5": len(last5) == 5 and all(g["gf"] > g["ga"] for g in last5),
                "loss_streak5": len(last5) == 5 and all(g["gf"] < g["ga"] for g in last5),
                "promoted_early": bool(prev_teams) and team not in prev_teams and games_this_season < 8,
                "conversion": goals / sot if len(last6) == 6 and sot >= 20 else None,
                "remaining": season_count[(r.competition, r.season, team)] - games_this_season - 1,
            }
        for team, gf, ga, sot, is_home in ((r.home, r.home_goals, r.away_goals, r.home_sot, True),
                                           (r.away, r.away_goals, r.home_goals, r.away_sot, False)):
            history[(r.competition, team)].append({"date": date, "gf": gf, "ga": ga, "sot": sot})
            played[(r.competition, r.season, team)] += 1


def _rest_gap(r: MatchRow) -> int | None:
    h, a = r.features["home"]["rest_days"], r.features["away"]["rest_days"]
    return None if h is None or a is None else h - a


FACTORS = [
    ("Heimteam nach 5 Siegen in Folge", "H", lambda r: r.features["home"]["win_streak5"]),
    ("Auswärtsteam nach 5 Siegen in Folge", "A", lambda r: r.features["away"]["win_streak5"]),
    ("Heimteam nach 5 Niederlagen in Folge", "H", lambda r: r.features["home"]["loss_streak5"]),
    ("Auswärtsteam nach 5 Niederlagen in Folge", "A", lambda r: r.features["away"]["loss_streak5"]),
    ("Aufsteiger zu Hause (erste 8 Spiele)", "H", lambda r: r.features["home"]["promoted_early"]),
    ("Aufsteiger auswärts (erste 8 Spiele)", "A", lambda r: r.features["away"]["promoted_early"]),
    ("Heimteam 3+ Ruhetage mehr (nur Ligaspiele)", "H", lambda r: (_rest_gap(r) or 0) >= 3),
    ("Auswärtsteam 3+ Ruhetage mehr (nur Ligaspiele)", "A", lambda r: (_rest_gap(r) or 0) <= -3),
    ("Heimteam traf zuletzt auffällig oft (Glückssträhne)", "H",
     lambda r: (r.features["home"]["conversion"] or 0) > 0.45),
    ("Heimteam traf zuletzt auffällig selten (Pechsträhne)", "H",
     lambda r: r.features["home"]["conversion"] is not None and r.features["home"]["conversion"] < 0.22),
    ("Auswärtsteam traf zuletzt auffällig oft (Glückssträhne)", "A",
     lambda r: (r.features["away"]["conversion"] or 0) > 0.45),
    ("Auswärtsteam traf zuletzt auffällig selten (Pechsträhne)", "A",
     lambda r: r.features["away"]["conversion"] is not None and r.features["away"]["conversion"] < 0.22),
    ("Letzte 4 Saisonspiele: Unentschieden", "D",
     lambda r: max(r.features["home"]["remaining"], r.features["away"]["remaining"]) < 4),
    ("Letzte 4 Saisonspiele: Über 2,5 Tore", "over",
     lambda r: max(r.features["home"]["remaining"], r.features["away"]["remaining"]) < 4),
    ("Ausgeglichenes Spiel (Heim 35–45 %): Unentschieden", "D",
     lambda r: "H" in r.bets and 0.35 <= r.bets["H"].p_market <= 0.45),
]


# --- Statistik ------------------------------------------------------------------------------

def stats(bets: list[Bet]) -> dict | None:
    if not bets:
        return None
    n = len(bets)
    expected = sum(b.p_market for b in bets)
    wins = sum(b.won for b in bets)
    variance = sum(b.p_market * (1 - b.p_market) for b in bets)
    result = {"n": n, "market": expected / n, "actual": wins / n,
              "z": (wins - expected) / math.sqrt(variance) if variance > 0 else 0.0}
    for key, attr in (("return_single", "price_single"), ("return_best", "price_best")):
        priced = [(getattr(b, attr), b.won) for b in bets if getattr(b, attr)]
        if len(priced) >= 30:
            values = [p if w else 0.0 for p, w in priced]
            mean = sum(values) / len(values)
            sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))
            result[key] = mean
            result[key + "_se"] = sd / math.sqrt(len(values))
    return result


def verdict(train: dict | None, test: dict | None) -> str:
    if not train or not test or train["n"] < 150 or test["n"] < 80:
        return "zu wenig Spiele"
    if abs(train["z"]) >= 2 and abs(test["z"]) >= 1.5 and train["z"] * test["z"] > 0:
        return "BESTÄTIGT"
    if abs(train["z"]) >= 2:
        return "nicht bestätigt"
    return "kein Effekt"


def run(conn: sqlite3.Connection) -> dict:
    rows = load(conn)
    add_features(rows)
    report: dict = {"generated": dt.datetime.now().isoformat(timespec="seconds"), "split_season": SPLIT_SEASON,
                    "matches": len(rows), "odds_buckets": [], "leagues": [], "factors": [], "calibration": []}

    def bets_for(selection, row_filter=lambda r: True, period=None):
        return [r.bets[selection] for r in rows
                if selection in r.bets and row_filter(r) and (period is None or r.period == period)]

    for selection, (_, label) in SELECTIONS.items():
        for low, high in ODDS_BUCKETS:
            in_bucket = lambda r, s=selection, lo=low, hi=high: r.bets[s].price_single and lo <= r.bets[s].price_single < hi
            train, test = stats(bets_for(selection, in_bucket, "lernen")), stats(bets_for(selection, in_bucket, "prüfen"))
            if train or test:
                report["odds_buckets"].append({"selection": selection, "label": label, "low": low, "high": high,
                                               "train": train, "test": test})

    for code, info in config.LEAGUES.items():
        for selection, (_, label) in SELECTIONS.items():
            league = lambda r, c=code: r.competition == c
            report["leagues"].append({"league": info["name"], "selection": selection, "label": label,
                                      "train": stats(bets_for(selection, league, "lernen")),
                                      "test": stats(bets_for(selection, league, "prüfen"))})

    for name, selection, condition in FACTORS:
        train = stats(bets_for(selection, condition, "lernen"))
        test = stats(bets_for(selection, condition, "prüfen"))
        report["factors"].append({"name": name, "selection": selection, "train": train, "test": test,
                                  "verdict": verdict(train, test)})

    all_bets = [b for r in rows for b in r.bets.values()]
    for low in range(0, 100, 10):
        bucket = [b for b in all_bets if low / 100 <= b.p_market < (low + 10) / 100]
        if bucket:
            report["calibration"].append({"low": low, "high": low + 10, **stats(bucket)})
    return report


def save(report: dict, path=None) -> None:
    path = path or (config.DATA_DIR / "research.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
