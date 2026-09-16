"""Datenquelle: ClubElo (kostenlos, ligaübergreifende Elo-Werte).

Das eigene Elo-Modell kennt nur Ligaspiele und kann deshalb Ligen nicht
untereinander vergleichen. ClubElo berücksichtigt Europapokalspiele und liefert
damit die Grundlage für Champions League, Europa League und Conference League.
Das CSV-Format wird beim ersten echten Abruf geprüft.
"""
from __future__ import annotations

import csv
import datetime as dt
import io

from fi import config, http


def fetch_ratings_csv(date: dt.date) -> bytes:
    return http.get_bytes(f"{config.CLUBELO_BASE_URL}/{date.isoformat()}")


def parse_ratings_csv(raw: bytes) -> list[dict]:
    rows = []
    for row in csv.DictReader(io.StringIO(raw.decode("utf-8-sig", errors="replace"))):
        try:
            rows.append({
                "club": row["Club"].strip(),
                "country": (row.get("Country") or "").strip() or None,
                "level": int(row["Level"]) if row.get("Level") else None,
                "elo": float(row["Elo"]),
            })
        except (KeyError, ValueError, AttributeError):
            continue
    return rows
