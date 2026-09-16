"""Datenquelle: football-data.co.uk (kostenlose CSV-Dateien).

Liefert pro Liga und Saison Ergebnisse, Schüsse und Quoten.
Hinweis zu den Quoten laut Anbieter: 'pre'-Quoten werden kurz vor dem Spieltag
erfasst (freitags bzw. dienstags), 'closing' sind die Schlussquoten bei Anpfiff.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
from pathlib import Path

from fi import config, http

SOURCE = "football-data.co.uk"
BASE_URL = "https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"


class NotAvailable(Exception):
    """Für diese Liga/Saison gibt es (noch) keine Datei."""


def _build_odds_columns() -> list[tuple[str, str, str, str, str]]:
    """(CSV-Spalte, Buchmacher, Zeitpunkt, Markt, Auswahl)."""
    columns = []
    one_x_two = [("B365", "bet365", "pre"), ("B365C", "bet365", "closing"),
                 ("PS", "pinnacle", "pre"), ("PSC", "pinnacle", "closing"),
                 ("Avg", "average", "pre"), ("AvgC", "average", "closing"),
                 ("Max", "maximum", "pre"), ("MaxC", "maximum", "closing")]
    for prefix, bookmaker, timing in one_x_two:
        for selection in "HDA":
            columns.append((f"{prefix}{selection}", bookmaker, timing, "1x2", selection))
    over_under = [("B365", "bet365", "pre"), ("B365C", "bet365", "closing"),
                  ("P", "pinnacle", "pre"), ("PC", "pinnacle", "closing"),
                  ("Avg", "average", "pre"), ("AvgC", "average", "closing"),
                  ("Max", "maximum", "pre"), ("MaxC", "maximum", "closing")]
    for prefix, bookmaker, timing in over_under:
        columns.append((f"{prefix}>2.5", bookmaker, timing, "ou25", "over"))
        columns.append((f"{prefix}<2.5", bookmaker, timing, "ou25", "under"))
    return columns


ODDS_COLUMNS = _build_odds_columns()


def download(code: str, season_start: int, raw_dir: Path = config.RAW_DIR,
             force: bool = False) -> bytes:
    """Lädt eine Saison-Datei. Abgeschlossene Saisons werden nur einmal geladen."""
    path = raw_dir / config.season_code(season_start) / f"{code}.csv"
    if path.exists() and not force:
        return path.read_bytes()
    url = BASE_URL.format(season=config.season_code(season_start), code=code)
    try:
        raw = http.get_bytes(url)
    except http.NotFound as exc:
        raise NotAvailable(url) from exc
    if b"HomeTeam" not in raw[:3000]:  # z.B. HTML-Fehlerseite statt CSV
        raise NotAvailable(url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def _parse_date(value: str) -> dt.date | None:
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _int(value: str | None) -> int | None:
    try:
        return int(float(value)) if value not in (None, "") else None
    except ValueError:
        return None


def _price(value: str | None) -> float | None:
    try:
        price = float(value) if value not in (None, "") else None
    except ValueError:
        return None
    return price if price is not None and price > 1.0 else None


def _rows(raw: bytes):
    for raw_row in csv.DictReader(io.StringIO(_decode(raw))):
        yield {k.strip(): (v or "").strip() for k, v in raw_row.items()
               if isinstance(k, str) and k.strip() and isinstance(v, (str, type(None)))}


def _parse_row(row: dict, code: str, season: str, require_result: bool) -> dict | None:
    home, away = row.get("HomeTeam", ""), row.get("AwayTeam", "")
    match_date = _parse_date(row.get("Date", ""))
    home_goals, away_goals = _int(row.get("FTHG")), _int(row.get("FTAG"))
    if not home or not away or match_date is None:
        return None
    if require_result and (home_goals is None or away_goals is None):
        return None
    odds = []
    for column, bookmaker, timing, market, selection in ODDS_COLUMNS:
        price = _price(row.get(column))
        if price is not None:
            odds.append((bookmaker, timing, market, selection, price))
    return {
        "competition": code,
        "season": season,
        "match_date": match_date.isoformat(),
        "kick_time": row.get("Time") or None,
        "home_team": home,
        "away_team": away,
        "home_goals": home_goals,
        "away_goals": away_goals,
        "home_shots": _int(row.get("HS")),
        "away_shots": _int(row.get("AS")),
        "home_shots_target": _int(row.get("HST")),
        "away_shots_target": _int(row.get("AST")),
        "odds": odds,
    }


def parse_results_csv(raw: bytes, code: str, season_start: int) -> list[dict]:
    """Saison-Datei -> einheitliche Match-Dicts. Nur gespielte Spiele."""
    season = config.season_code(season_start)
    parsed = (_parse_row(row, code, season, require_result=True) for row in _rows(raw))
    return [m for m in parsed if m is not None]


FIXTURES_URL = "https://www.football-data.co.uk/fixtures.csv"


def download_fixtures() -> bytes:
    """Kommende Spiele aller Ligen inkl. aktueller Quoten (Anbieter aktualisiert mehrmals pro Woche)."""
    raw = http.get_bytes(FIXTURES_URL)
    if b"HomeTeam" not in raw[:3000]:
        raise NotAvailable(FIXTURES_URL)
    return raw


def parse_fixtures_csv(raw: bytes, leagues) -> list[dict]:
    """Kommende Spiele, nur für die angegebenen Liga-Codes. Tore bleiben leer."""
    fixtures = []
    for row in _rows(raw):
        code = row.get("Div", "")
        match_date = _parse_date(row.get("Date", ""))
        if code not in leagues or match_date is None:
            continue
        season = config.season_code(config.season_start_for(match_date))
        match = _parse_row(row, code, season, require_result=False)
        if match is not None:
            match["home_goals"] = match["away_goals"] = None
            fixtures.append(match)
    return fixtures
