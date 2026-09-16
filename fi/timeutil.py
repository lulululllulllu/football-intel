"""Zeitumrechnung.

football-data.co.uk und dieses Projekt speichern Anstoßzeiten in britischer Zeit.
Deutschland liegt immer genau eine Stunde dahinter, weil beide Länder die Sommerzeit
am selben Tag und zum selben Zeitpunkt umstellen.
"""
from __future__ import annotations

import datetime as dt


def uk_to_berlin(match_date: str, kick_time: str | None) -> dt.datetime | None:
    if not kick_time:
        return None
    return dt.datetime.fromisoformat(f"{match_date}T{kick_time}") + dt.timedelta(hours=1)


def berlin_label(match_date: str, kick_time: str | None) -> str:
    local = uk_to_berlin(match_date, kick_time)
    weekdays = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    if local is None:
        date = dt.date.fromisoformat(match_date)
        return f"{weekdays[date.weekday()]} {date:%d.%m.} --:--"
    return f"{weekdays[local.weekday()]} {local:%d.%m. %H:%M}"


def utc_to_berlin(moment: dt.datetime) -> dt.datetime:
    from fi.providers.odds_api import to_uk_time
    return to_uk_time(moment) + dt.timedelta(hours=1)
