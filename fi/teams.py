"""Zuordnung von Teamnamen zwischen Datenquellen.

Beispiel: Odds-API.io schreibt "1. FC Köln", football-data.co.uk "FC Koln".
Vorgehen:
1. Gespeicherte Zuordnung verwenden (manuell hat Vorrang vor automatisch)
2. Sonst Namen vereinfachen (Akzente, Kürzel wie FC/SV, Abkürzungen ausschreiben)
   und nur mit den Teams DERSELBEN Liga vergleichen
3. Nur eindeutige Treffer werden übernommen. Unsichere Fälle bleiben offen
   und werden angezeigt, statt geraten zu werden.
"""
from __future__ import annotations

import difflib
import re
import sqlite3
import unicodedata

from fi import db

# Abkürzungen, wie sie vor allem bei football-data.co.uk vorkommen
ABBREVIATIONS = {
    "man": "manchester", "utd": "united", "nott'm": "nottingham", "ath": "athletic",
    "atletico": "athletic", "ein": "eintracht", "m'gladbach": "monchengladbach",
    "gladbach": "monchengladbach", "sp": "sporting", "psg": "paris saint germain",
    "sg": "saint germain", "st": "saint", "wolves": "wolverhampton wanderers",
    "spurs": "tottenham hotspur", "munchen": "munich", "koeln": "koln", "cologne": "koln",
}
STOPWORDS = {"fc", "cf", "afc", "sc", "ac", "as", "ss", "ssc", "sv", "vfb", "vfl", "tsg", "rc",
             "rcd", "ud", "cd", "sd", "club", "calcio", "de", "hotspur", "wanderers", "and"}

MIN_SCORE = 0.75
MIN_GAP = 0.08


def tokens(name: str) -> list[str]:
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = re.sub(r"[^a-z0-9' ]+", " ", text.replace("-", " ").replace(".", " "))
    result = []
    for raw in text.split():
        for word in ABBREVIATIONS.get(raw, raw).split():
            word = word.replace("'", "")
            if word and word not in STOPWORDS and not word.isdigit():
                result.append(word)
    return result


def similarity(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    overlap = sum(1 for t in ta if any(t == u or (len(t) >= 4 and len(u) >= 4 and
                                                  (t.startswith(u) or u.startswith(t))) for u in tb))
    containment = min(overlap, len(tb)) / min(len(ta), len(tb))
    sequence = difflib.SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio()
    return 0.6 * containment + 0.4 * sequence


def best_match(name: str, candidates) -> tuple[str | None, float]:
    scored = sorted(((similarity(name, c), c) for c in candidates), reverse=True)
    if not scored:
        return None, 0.0
    best_score, best = scored[0]
    second = scored[1][0] if len(scored) > 1 else 0.0
    if best_score >= MIN_SCORE and best_score - second >= MIN_GAP:
        return best, best_score
    return None, best_score


def current_teams(conn: sqlite3.Connection, competition: str | None) -> list[str]:
    """Teams der jeweils neuesten Saison einer Liga (oder aller Ligen bei None)."""
    query = ("SELECT DISTINCT team FROM (SELECT home_team AS team, competition, season FROM matches "
             "UNION SELECT away_team, competition, season FROM matches) t "
             "WHERE season = (SELECT MAX(season) FROM matches m WHERE m.competition = t.competition)")
    params: tuple = ()
    if competition:
        query += " AND competition = ?"
        params = (competition,)
    return [r[0] for r in conn.execute(query, params)]


def resolve(conn: sqlite3.Connection, source: str, name: str, competition: str | None) -> str | None:
    row = conn.execute("SELECT canonical FROM team_name_map WHERE source=? AND source_name=?",
                       (source, name)).fetchone()
    if row:
        return row["canonical"]
    match, score = best_match(name, current_teams(conn, competition))
    if match:
        with conn:
            conn.execute("INSERT OR IGNORE INTO team_name_map VALUES (?, ?, ?, 'auto', ?, ?)",
                         (source, name, match, score, db.utc_now()))
    return match


def set_manual(conn: sqlite3.Connection, source: str, name: str, canonical: str) -> None:
    with conn:
        conn.execute("INSERT OR REPLACE INTO team_name_map VALUES (?, ?, ?, 'manuell', NULL, ?)",
                     (source, name, canonical, db.utc_now()))
