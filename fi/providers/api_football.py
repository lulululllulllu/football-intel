"""Datenquelle: API-Football (Gratis-Tarif: 100 Anfragen pro Tag).

Wird sparsam eingesetzt: tägliche Spielpläne sowie Aufstellungen und
Verletzungen nur für die Kandidaten-Spiele des Optimierers.
Der API-Schlüssel kommt aus der Umgebungsvariable APIFOOTBALL_KEY, nie aus dem Code.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import urllib.parse

from fi import config, http

API_NAME = "api-football"


class QuotaExceeded(Exception):
    """Tageslimit (abzüglich Reserve) erreicht, es wurde keine Anfrage gesendet."""


class ApiError(Exception):
    """Die API hat einen Fehler gemeldet (z.B. Saison im Gratis-Tarif gesperrt)."""


def raise_on_errors(payload: dict) -> None:
    errors = payload.get("errors")
    if errors:  # API-Football liefert [] oder {} ohne Fehler, sonst Liste/Dict mit Meldungen
        raise ApiError(json.dumps(errors, ensure_ascii=False))


def normalize_fixture(item: dict) -> dict:
    fixture, league, teams, goals = item["fixture"], item["league"], item["teams"], item.get("goals", {})
    return {
        "fixture_id": fixture["id"],
        "kickoff": fixture["date"],
        "status": fixture.get("status", {}).get("short"),
        "league_id": league["id"],
        "league": league.get("name"),
        "season": league.get("season"),
        "home_team": teams["home"]["name"],
        "away_team": teams["away"]["name"],
        "home_goals": goals.get("home"),
        "away_goals": goals.get("away"),
    }


class ApiFootball:
    def __init__(self, api_key: str, conn: sqlite3.Connection,
                 daily_limit: int = config.API_FOOTBALL_DAILY_LIMIT,
                 reserve: int = config.API_FOOTBALL_RESERVE):
        self.api_key = api_key
        self.conn = conn
        self.budget = daily_limit - reserve

    # --- Kontingent ------------------------------------------------------------
    def _today(self) -> str:
        return dt.datetime.now(dt.timezone.utc).date().isoformat()

    def used_today(self) -> int:
        row = self.conn.execute(
            "SELECT requests FROM api_usage WHERE api=? AND day=?", (API_NAME, self._today())
        ).fetchone()
        return row["requests"] if row else 0

    def _count_request(self) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO api_usage (api, day, requests) VALUES (?, ?, 1) "
                "ON CONFLICT (api, day) DO UPDATE SET requests = requests + 1",
                (API_NAME, self._today()),
            )

    # --- Anfragen --------------------------------------------------------------
    def _get(self, path: str, params: dict | None = None) -> list:
        if self.used_today() >= self.budget:
            raise QuotaExceeded(f"{self.used_today()} von {self.budget} Anfragen heute verbraucht")
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        self._count_request()  # vor dem Senden zählen: lieber zu vorsichtig als über dem Limit
        raw = http.get_bytes(f"{config.API_FOOTBALL_BASE_URL}{path}{query}",
                             headers={"x-apisports-key": self.api_key})
        payload = json.loads(raw)
        raise_on_errors(payload)
        return payload.get("response", [])

    def status(self) -> dict:
        response = self._get("/status")
        return response if isinstance(response, dict) else (response[0] if response else {})

    def fixtures_on(self, date: dt.date) -> list[dict]:
        """Alle Spiele eines Tages, gefiltert auf die konfigurierten Wettbewerbe (1 Anfrage)."""
        wanted = {c["api_football_id"] for c in (*config.LEAGUES.values(), *config.CUPS.values())}
        items = self._get("/fixtures", {"date": date.isoformat(), "timezone": config.TIMEZONE})
        return [normalize_fixture(i) for i in items if i["league"]["id"] in wanted]

    def lineups(self, fixture_id: int) -> list:
        return self._get("/fixtures/lineups", {"fixture": fixture_id})

    def injuries(self, fixture_id: int) -> list:
        return self._get("/injuries", {"fixture": fixture_id})
