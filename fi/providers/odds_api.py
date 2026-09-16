"""Datenquelle: The Odds API (the-odds-api.com).

Gratis-Tarif: 500 Credits pro Monat.
- /sports und /events sind kostenlos
- /odds kostet [Anzahl Märkte] x [Regionen]; bis zu 10 Buchmacher zählen als 1 Region
- Antworten ohne Spiele kosten nichts
Deshalb wird pro Wettbewerb zuerst kostenlos geprüft, ob überhaupt Spiele anstehen.
Der Schlüssel kommt aus der Umgebungsvariable ODDS_API_KEY, nie aus dem Code.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import urllib.parse

from fi import config, http

API_NAME = "the-odds-api.com"


def parse_utc(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def _last_sunday(year: int, month: int) -> dt.date:
    day = dt.date(year, month + 1, 1) - dt.timedelta(days=1)
    return day - dt.timedelta(days=(day.weekday() + 1) % 7)


def to_uk_time(moment: dt.datetime) -> dt.datetime:
    """UTC -> britische Zeit. football-data.co.uk speichert Datum und Uhrzeit in UK-Zeit."""
    start = dt.datetime.combine(_last_sunday(moment.year, 3), dt.time(1), dt.timezone.utc)
    end = dt.datetime.combine(_last_sunday(moment.year, 10), dt.time(1), dt.timezone.utc)
    offset = dt.timedelta(hours=1) if start <= moment < end else dt.timedelta(0)
    return (moment + offset).replace(tzinfo=None)


def _iso(moment: dt.datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_odds(event: dict) -> list[tuple[str, str, str, float, str | None]]:
    """-> Liste (Buchmacher, Markt, Auswahl, Quote, Stand laut Anbieter)."""
    home, away = event.get("home_team"), event.get("away_team")
    result = []
    for bookmaker in event.get("bookmakers") or []:
        key = bookmaker.get("key", "").lower()
        for market in bookmaker.get("markets") or []:
            updated = market.get("last_update") or bookmaker.get("last_update")
            for outcome in market.get("outcomes") or []:
                price, name = outcome.get("price"), outcome.get("name")
                if not isinstance(price, (int, float)) or price <= 1.0:
                    continue
                if market.get("key") == "h2h":
                    selection = {home: "H", away: "A", "Draw": "D"}.get(name)
                    if selection:
                        result.append((key, "1x2", selection, float(price), updated))
                elif market.get("key") == "totals" and outcome.get("point") == 2.5 and name in ("Over", "Under"):
                    result.append((key, "ou25", name.lower(), float(price), updated))
    return result


class OddsApi:
    def __init__(self, api_key: str, conn: sqlite3.Connection):
        self.api_key, self.conn = api_key, conn
        self.remaining: int | None = None
        self.used_last_call: int = 0

    def _get(self, path: str, params: dict | None = None):
        query = urllib.parse.urlencode({**(params or {}), "apiKey": self.api_key})
        data, headers = http.get_json_with_headers(f"{config.ODDS_API_BASE_URL}{path}?{query}")
        if headers.get("x-requests-remaining") is not None:
            self.remaining = int(float(headers["x-requests-remaining"]))
        self.used_last_call = int(float(headers.get("x-requests-last") or 0))
        return data

    def sports(self) -> list[dict]:
        """Kostenlos."""
        return [s for s in self._get("/sports", {"all": "true"}) if isinstance(s, dict)]

    def events(self, sport_key: str, until: dt.datetime) -> list[dict]:
        """Kostenlos: kommende Spiele ohne Quoten."""
        now = dt.datetime.now(dt.timezone.utc)
        return self._get(f"/sports/{sport_key}/events", {
            "commenceTimeFrom": _iso(now), "commenceTimeTo": _iso(until)})

    def odds(self, sport_key: str, until: dt.datetime, markets: list[str], bookmakers: list[str] | None,
             regions: str | None = None) -> list[dict]:
        """Kostet Credits: Anzahl Märkte x Regionen."""
        params = {"markets": ",".join(markets), "oddsFormat": "decimal",
                  "commenceTimeFrom": _iso(dt.datetime.now(dt.timezone.utc)), "commenceTimeTo": _iso(until)}
        if bookmakers:
            params["bookmakers"] = ",".join(bookmakers[:config.ODDS_API_MAX_BOOKMAKERS])
        else:
            params["regions"] = regions or "eu"
        return self._get(f"/sports/{sport_key}/odds", params)


def choose_bookmakers(events: list[dict]) -> list[tuple[str, str]]:
    """Wählt bis zu 10 Buchmacher: bevorzugte zuerst, dann die mit den meisten Spielen."""
    counts: dict[str, list] = {}
    for event in events:
        for b in event.get("bookmakers") or []:
            counts.setdefault(b["key"], [b.get("title", b["key"]), 0])[1] += 1
    preferred = [k for k in config.ODDS_API_PREFERRED_BOOKMAKERS if k in counts]
    others = sorted((k for k in counts if k not in preferred), key=lambda k: -counts[k][1])
    return [(k, counts[k][0]) for k in (preferred + others)[:config.ODDS_API_MAX_BOOKMAKERS]]


def find_sport_key(sports: list[dict], competition: str) -> tuple[str | None, list[dict]]:
    keys = {s.get("key") for s in sports}
    default = config.ODDS_API_SPORT_KEYS[competition]
    if default in keys:
        return default, []
    words = config.ODDS_API_SEARCH_WORDS[competition]
    candidates = [s for s in sports if s.get("key", "").startswith("soccer_")
                  and any(w in s.get("key", "") or w in s.get("title", "").lower() for w in words)]
    return None, candidates


def _scores_method(self, sport_key: str) -> list[dict]:
    """Ergebnisse der letzten 3 Tage. Kostet 2 Credits."""
    return self._get(f"/sports/{sport_key}/scores", {"daysFrom": 3})


OddsApi.scores = _scores_method
