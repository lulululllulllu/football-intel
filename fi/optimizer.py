"""Optimierer: Welche Wette hat für eine Zielquote die höchste echte Gewinnchance?

Grundlage (siehe Backtest): Die Wahrscheinlichkeiten kommen aus dem MARKT, also dem
Mittel vieler Buchmacher ohne Marge. Das eigene Modell dient nur als Warnsystem.

Ablauf:
1. Kandidaten sammeln: kommende Spiele mit frischen Quoten, von genug Buchmachern angeboten
2. Für jede Auswahl die beste Quote bei DEINEN Buchmachern suchen
3. Auswahl streichen, wenn das Modell stark vom Markt abweicht (Hinweis auf fehlende Information)
4. Einzelwetten und Kombis (je Spiel höchstens ein Tipp) bilden, deren Gesamtquote zur Zielquote passt
5. Bewertet wird die erwartete Rückzahlung = Gewinnchance x Quote. Bei gleicher Quote heißt höher:
   höhere Gewinnchance. Weil sich die Marge jedes Tipps multipliziert, gewinnen kleine Kombis meist.
6. Liegt nichts über der Mindestgrenze: "Heute keine Wette"
"""
from __future__ import annotations

import datetime as dt
import itertools
import json
import sqlite3
from dataclasses import asdict, dataclass, field

from fi import adjust, config, db
from fi.predict import market_probabilities, predict_upcoming
from fi.providers.odds_api import to_uk_time
from fi.timeutil import berlin_label

SELECTION_LABELS = {"H": "Sieg {home}", "D": "Unentschieden", "A": "Sieg {away}",
                    "over": "Über 2,5 Tore", "under": "Unter 2,5 Tore"}
POOL_SIZE = 60


@dataclass
class Leg:
    match_id: int
    competition: str
    kickoff: str
    home: str
    away: str
    market: str
    selection: str
    price: float
    bookmaker: str
    p_market: float
    p_model: float | None
    n_bookmakers: int
    prices: dict = field(default_factory=dict)   # Quote je Buchmacher (alle, nicht nur deine)
    p_raw: float | None = None                   # Marktwahrscheinlichkeit vor den Korrekturen
    hints: list = field(default_factory=list)

    @property
    def match_date(self) -> str:
        return self.kickoff[:10]

    @property
    def kickoff_local(self) -> str:
        return berlin_label(self.kickoff[:10], self.kickoff[11:16])

    @property
    def label(self) -> str:
        return SELECTION_LABELS[self.selection].format(home=self.home, away=self.away)

    @property
    def expected_return(self) -> float:
        return self.p_market * self.price


@dataclass
class Suggestion:
    legs: list[Leg]

    @property
    def total_odds(self) -> float:
        result = 1.0
        for leg in self.legs:
            result *= leg.price
        return result

    @property
    def win_probability(self) -> float:
        """Spiele sind verschieden, daher unabhängig: Wahrscheinlichkeiten werden multipliziert."""
        result = 1.0
        for leg in self.legs:
            result *= leg.p_market
        return result

    @property
    def expected_return(self) -> float:
        return self.win_probability * self.total_odds


def my_bookmakers(conn: sqlite3.Connection) -> list[str]:
    row = conn.execute("SELECT external_id FROM source_leagues WHERE source='me' AND competition='bookmakers'"
                       ).fetchone()
    return json.loads(row["external_id"]) if row else list(config.MY_BOOKMAKERS)


def set_my_bookmakers(conn: sqlite3.Connection, keys: list[str]) -> None:
    with conn:
        conn.execute("INSERT OR REPLACE INTO source_leagues VALUES ('me', 'bookmakers', ?)", (json.dumps(keys),))


def collect_legs(conn: sqlite3.Connection, now_utc: dt.datetime, days: int,
                 allowed: list[str], use_model: bool = True,
                 include_unpriced: bool = False) -> tuple[list[Leg], list[str]]:
    now_uk = to_uk_time(now_utc)
    today = now_uk.date()
    notes: list[str] = []
    model_by_match = {}
    if use_model:
        for item in predict_upcoming(conn, today, days, list(config.LEAGUES)):
            if "model" in item:
                model_by_match[item["match"]["id"]] = item["model"]

    legs = []
    rows = conn.execute("SELECT * FROM matches WHERE home_goals IS NULL AND match_date BETWEEN ? AND ?",
                        (today.isoformat(), (today + dt.timedelta(days=days)).isoformat())).fetchall()
    max_age = dt.timedelta(hours=config.MAX_ODDS_AGE_HOURS)
    for m in rows:
        if not m["kick_time"]:
            continue
        kickoff = dt.datetime.fromisoformat(f"{m['match_date']}T{m['kick_time']}")
        if kickoff <= now_uk + dt.timedelta(minutes=15):
            continue
        match_legs, suspicious = [], None
        for market, info in market_probabilities(conn, m["id"]).items():
            if market not in ("1x2", "ou25") or not info.get("fetched_at"):
                continue
            if now_utc - dt.datetime.fromisoformat(info["fetched_at"]) > max_age:
                continue
            n_books = len(info["bookmaker"].split("/"))
            if n_books < config.MIN_BOOKMAKERS_FOR_PROB:
                continue
            adjusted, excluded, adjust_notes = adjust.adjust(conn, m, market, info["probs"])
            for text in adjust_notes:
                if "ausgeschlossen" in text:
                    notes.append(f"{m['home_team']} – {m['away_team']}: {text}")
            for selection, p_raw in info["probs"].items():
                if selection in excluded:
                    continue
                p_market = adjusted[selection]
                prices = {r["bookmaker"]: r["price"] for r in conn.execute(
                    "SELECT bookmaker, price FROM odds WHERE match_id=? AND timing='current' AND market=? "
                    "AND selection=?", (m["id"], market, selection))}
                mine = {b: p for b, p in prices.items() if b in allowed}
                p_model = model_by_match.get(m["id"], {}).get(market, {}).get(selection)
                leg = Leg(m["id"], m["competition"], kickoff.isoformat(" ", "minutes"), m["home_team"],
                          m["away_team"], market, selection, 0.0, "", p_market, p_model, n_books, prices,
                          p_raw, [t for t in adjust_notes if "ausgeschlossen" not in t])
                if p_model is not None and abs(p_model - p_raw) > config.MAX_MODEL_GAP:
                    suspicious = (leg, p_raw, p_model)
                if mine:
                    bookmaker, price = max(mine.items(), key=lambda item: item[1])
                    if price <= config.MAX_LEG_ODDS:  # Außenseiter werden nicht empfohlen
                        leg.bookmaker, leg.price = bookmaker, price
                match_legs.append(leg)
        if suspicious:
            # Weicht das Modell bei einem Ausgang stark ab, fehlt ihm Information über das ganze Spiel
            leg, p_market, p_model = suspicious
            notes.append(f"Spiel ausgeschlossen: {leg.home} – {leg.away} ({leg.label}: Markt {p_market:.0%}, "
                         f"Modell {p_model:.0%})")
            continue
        legs += [l for l in match_legs if l.price > 0 or include_unpriced]
    return legs, notes


def optimize(legs: list[Leg], target: float, max_legs: int = 3, tolerance: float = config.TARGET_TOLERANCE,
             count: int = 3) -> list[Suggestion]:
    low, high = target * (1 - tolerance), target * (1 + tolerance)
    pool = sorted((l for l in legs if l.price > 0), key=lambda l: l.expected_return, reverse=True)[:POOL_SIZE]
    found = []
    for size in range(1, max_legs + 1):
        for combo in itertools.combinations(pool, size):
            if len({leg.match_id for leg in combo}) < size:
                continue  # je Spiel nur ein Tipp: sonst hängen die Ereignisse voneinander ab
            suggestion = Suggestion(list(combo))
            if low <= suggestion.total_odds <= high:
                found.append(suggestion)
    found.sort(key=lambda s: s.expected_return, reverse=True)

    chosen: list[Suggestion] = []
    for suggestion in found:
        keys = {(l.match_id, l.market, l.selection) for l in suggestion.legs}
        if any(keys == {(l.match_id, l.market, l.selection) for l in c.legs} for c in chosen):
            continue
        chosen.append(suggestion)
        if len(chosen) == count:
            break
    return chosen


def best_in_range(legs: list[Leg], low: float, high: float, max_legs: int = 1,
                  min_return: float = config.MIN_EXPECTED_RETURN) -> Suggestion | None:
    """Wette des Tages: höchste Gewinnchance mit Gesamtquote zwischen low und high.

    Nur Wetten mit ausreichender erwarteter Rückzahlung kommen in Frage. Bei gleicher Chance
    gewinnt die mit der besseren Rückzahlung.
    """
    priced = [l for l in legs if l.price > 0]
    by_return = sorted(priced, key=lambda l: l.expected_return, reverse=True)[:40]
    by_chance = sorted(priced, key=lambda l: l.p_market, reverse=True)[:40]
    pool = list({id(l): l for l in by_return + by_chance}.values())
    best = None
    for size in range(1, max_legs + 1):
        for combo in itertools.combinations(pool, size):
            if len({leg.match_id for leg in combo}) < size:
                continue
            s = Suggestion(list(combo))
            if not low <= s.total_odds <= high or s.expected_return < min_return:
                continue
            if best is None or (s.win_probability, s.expected_return) > (best.win_probability, best.expected_return):
                best = s
    return best


def save(conn: sqlite3.Connection, target: float, suggestion: Suggestion) -> None:
    with conn:
        conn.execute(
            "INSERT INTO recommendations (created_at, target_odds, total_odds, win_probability, expected_return, legs) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (db.utc_now(), target, suggestion.total_odds, suggestion.win_probability,
             suggestion.expected_return, json.dumps([asdict(l) for l in suggestion.legs], ensure_ascii=False)))
