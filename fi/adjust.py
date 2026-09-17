"""Korrekturen der Marktwahrscheinlichkeiten auf Basis der historischen Untersuchung.

Alle Werte stehen in config.py. Jede Korrektur ist bewusst vorsichtig (kleiner als gemessen),
weil gemessene Effekte in der Zukunft meist schwächer ausfallen.
"""
from __future__ import annotations

import sqlite3

from fi import config


def _interpolate(p: float) -> float:
    points = config.CALIBRATION_POINTS
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= p <= x1:
            return y0 + (y1 - y0) * (p - x0) / (x1 - x0)
    return p


def calibrate(probs: dict[str, float]) -> dict[str, float]:
    """Favoriten etwas höher, Außenseiter etwas niedriger bewerten, dann wieder auf 100 % bringen."""
    shifted = {s: p + config.CALIBRATION_STRENGTH * (_interpolate(p) - p) for s, p in probs.items()}
    total = sum(shifted.values())
    return {s: p / total for s, p in shifted.items()}


def _boost(probs: dict[str, float], selection: str, amount: float) -> dict[str, float]:
    others = [s for s in probs if s != selection]
    rest = sum(probs[s] for s in others)
    boosted = {selection: min(probs[selection] + amount, 0.99)}
    for s in others:
        boosted[s] = probs[s] * (1 - boosted[selection]) / rest if rest else 0.0
    return boosted


def loss_streak(conn: sqlite3.Connection, team: str, before_date: str) -> int:
    """Niederlagen in Folge vor einem Datum (über alle gespeicherten Wettbewerbe)."""
    streak = 0
    for r in conn.execute(
            "SELECT home_team, home_goals, away_goals FROM matches WHERE (home_team=? OR away_team=?) "
            "AND home_goals IS NOT NULL AND match_date < ? ORDER BY match_date DESC LIMIT 10",
            (team, team, before_date)):
        own, other = ((r["home_goals"], r["away_goals"]) if r["home_team"] == team
                      else (r["away_goals"], r["home_goals"]))
        if own < other:
            streak += 1
        else:
            break
    return streak


def season_remaining(conn: sqlite3.Connection, competition: str, season: str, team: str) -> int | None:
    """Verbleibende Ligaspiele: 2 x (Teams - 1) minus bereits gespielte."""
    if competition not in config.LEAGUES:
        return None
    teams = conn.execute(
        "SELECT COUNT(*) FROM (SELECT home_team FROM matches WHERE competition=? AND season=? "
        "UNION SELECT away_team FROM matches WHERE competition=? AND season=?)",
        (competition, season, competition, season)).fetchone()[0]
    played = conn.execute(
        "SELECT COUNT(*) FROM matches WHERE competition=? AND season=? AND home_goals IS NOT NULL "
        "AND (home_team=? OR away_team=?)", (competition, season, team, team)).fetchone()[0]
    return None if teams < 10 else 2 * (teams - 1) - played


def adjust(conn: sqlite3.Connection, match, market: str, probs: dict[str, float]) -> tuple[dict, set, list[str]]:
    """-> (korrigierte Wahrscheinlichkeiten, ausgeschlossene Auswahlen, Hinweise)."""
    notes, excluded = [], set()
    adjusted = calibrate(probs)
    if market == "1x2":
        if 0.35 <= probs.get("H", 0) <= 0.45:
            adjusted = _boost(adjusted, "D", config.BOOST_BALANCED_DRAW)
            notes.append("ausgeglichenes Spiel: Unentschieden leicht höher bewertet")
        for selection, team in (("H", match["home_team"]), ("A", match["away_team"])):
            streak = loss_streak(conn, team, match["match_date"])
            if streak >= config.LOSS_STREAK_EXCLUDE:
                excluded.add(selection)
                notes.append(f"{team}: {streak} Niederlagen in Folge, Sieg-Tipp ausgeschlossen")
    if market == "ou25":
        remaining = [season_remaining(conn, match["competition"], match["season"], t)
                     for t in (match["home_team"], match["away_team"])]
        if all(r is not None for r in remaining) and max(remaining) <= 4:
            adjusted = _boost(adjusted, "over", config.BOOST_SEASON_END_OVER)
            notes.append("Saisonende: Über 2,5 leicht höher bewertet")
    return adjusted, excluded, notes


def fit_goal_expectations(probs_1x2: dict | None, probs_ou: dict | None,
                          fallback_total: float = 2.7) -> tuple[float, float] | None:
    """Welche erwarteten Tore je Team passen am besten zu den Marktquoten?

    Einfache Mustersuche über (Heimtore, Auswärtstore). Fehlt Über/Unter, wird die
    Gesamttorzahl schwach Richtung `fallback_total` gezogen.
    """
    from fi.models.goals import markets, score_matrix
    if not probs_1x2:
        return None

    def error(lh: float, la: float) -> float:
        m = markets(score_matrix(lh, la, -0.05, 8))
        e = sum((m["1x2"][s] - probs_1x2[s]) ** 2 for s in ("H", "D", "A"))
        if probs_ou:
            e += (m["ou25"]["over"] - probs_ou["over"]) ** 2
        else:
            e += 0.001 * (lh + la - fallback_total) ** 2
        return e

    lh, la, step = 1.45, 1.15, 0.4
    best = error(lh, la)
    while step > 0.005:
        improved = False
        for dh, da in ((step, 0), (-step, 0), (0, step), (0, -step), (step, -step), (-step, step)):
            nh, na = lh + dh, la + da
            if 0.05 <= nh <= 6 and 0.05 <= na <= 6:
                e = error(nh, na)
                if e < best:
                    lh, la, best, improved = nh, na, e, True
        if not improved:
            step /= 2
    return round(lh, 3), round(la, 3)
