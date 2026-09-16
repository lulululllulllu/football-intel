"""Tageslauf für GitHub Actions (und lokal mit `python -m fi website`).

1. Einstellungen aus settings.json übernehmen
2. Ergebnisse und Spielplan laden, Quoten abrufen (falls Schlüssel vorhanden)
3. Offene Empfehlungen auswerten
4. Für jede Zielquote die beste Wette suchen und speichern
5. Website docs/index.html erzeugen
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os

from fi import config, db, odds_sync, optimizer, site, teams, tracking
from fi.predict import market_probabilities, predict_upcoming
from fi.providers import odds_api
from fi.timeutil import berlin_label, utc_to_berlin

SETTINGS_PATH = config.PROJECT_DIR / "settings.json"
RECS_PATH = config.PROJECT_DIR / "state" / "recommendations.json"
SITE_PATH = config.PROJECT_DIR / "docs" / "index.html"


def load_settings() -> dict:
    return json.loads(SETTINGS_PATH.read_text(encoding="utf-8")) if SETTINGS_PATH.exists() else {}


def run(fetch_data: bool = True, fetch_odds: bool = True) -> dict:
    settings = load_settings()
    conn = db.connect()
    now = dt.datetime.now(dt.timezone.utc)
    today = utc_to_berlin(now).date()
    days = int(settings.get("tage_voraus", 2))
    mine = settings.get("meine_buchmacher") or optimizer.my_bookmakers(conn)
    optimizer.set_my_bookmakers(conn, mine)
    if settings.get("quoten_buchmacher"):
        odds_sync.set_bookmakers(conn, settings["quoten_buchmacher"])
    for source_name, canonical in (settings.get("team_zuordnungen") or {}).items():
        teams.set_manual(conn, odds_sync.SOURCE, source_name, canonical)
    status = {"credits": None, "odds_error": None, "notes": []}

    if fetch_data:
        from fi.cli import cmd_update
        cmd_update(argparse.Namespace(league=None, from_season=config.season_start_for(today) - 3))

    api = None
    key = os.environ.get("ODDS_API_KEY")
    if fetch_odds and key:
        api = odds_api.OddsApi(key, conn)
        try:
            summary = odds_sync.sync(conn, api, days)
            for code, info in summary["leagues"].items():
                for name in sorted(set(info["unresolved"])):
                    status["notes"].append(f"Team nicht zugeordnet: {name}. In settings.json unter "
                                           f"team_zuordnungen eintragen.")
        except Exception as exc:  # Website trotzdem bauen, Fehler sichtbar machen
            status["odds_error"] = str(exc)
    elif fetch_odds:
        status["odds_error"] = "Kein Schlüssel für the-odds-api.com hinterlegt."

    recs = tracking.load(RECS_PATH)
    if api:
        try:
            tracking.fetch_cup_results(conn, api, recs, now)
        except Exception as exc:
            status["notes"].append(f"Europapokal-Ergebnisse nicht abrufbar: {exc}")
        status["credits"] = api.remaining
    tracking.settle(conn, recs, today)

    legs, notes = optimizer.collect_legs(conn, now, days, mine, include_unpriced=True)
    status["notes"] += notes
    created_at = db.utc_now()
    targets = []
    for target in settings.get("zielquoten", [3.0]):
        suggestions = optimizer.optimize(legs, float(target), int(settings.get("max_tipps", 3)))
        best = suggestions[0] if suggestions else None
        if best and best.expected_return >= config.MIN_EXPECTED_RETURN:
            tracking.add(recs, float(target), best, created_at)
            targets.append({"target": target, "suggestion": site.suggestion_json(best), "reason": None})
        else:
            reason = ("Keine Kombination erreicht diese Quote." if not best else
                      f"Die beste Möglichkeit zahlt im Schnitt nur {best.expected_return:.0%} des Einsatzes zurück.")
            targets.append({"target": target, "suggestion": None, "reason": reason})
    tracking.save(RECS_PATH, recs)

    matches = []
    models = {i["match"]["id"]: i for i in predict_upcoming(conn, today, days, list(config.LEAGUES))}
    names = {**config.LEAGUES, **config.CUPS}
    for m in conn.execute("SELECT * FROM matches WHERE home_goals IS NULL AND match_date BETWEEN ? AND ? "
                          "ORDER BY match_date, kick_time", (today.isoformat(),
                                                            (today + dt.timedelta(days=days)).isoformat())):
        market = market_probabilities(conn, m["id"])
        item = models.get(m["id"], {})
        matches.append({
            "kickoff_local": berlin_label(m["match_date"], m["kick_time"]),
            "competition": names[m["competition"]]["name"], "home": m["home_team"], "away": m["away_team"],
            "market": {k: v["probs"] for k, v in market.items() if k in ("1x2", "ou25")},
            "model": {k: v for k, v in item.get("model", {}).items() if k in ("1x2", "ou25", "btts")},
            "low_data": any(n < config.LOW_DATA_THRESHOLD for n in item.get("effective_games", ())),
        })

    data = {
        "generated_local": utc_to_berlin(now).strftime("%d.%m.%Y, %H:%M"),
        "credits": status["credits"], "odds_error": status["odds_error"], "notes": status["notes"][:12],
        "targets": targets, "legs": [site.leg_json(l) for l in legs], "matches": matches,
        "track": list(reversed(recs))[:40], "summary": tracking.summary(recs),
        "my_bookmakers": mine,
        "all_bookmakers": sorted({b for l in legs for b in l.prices}),
        "rules": {"tolerance": config.TARGET_TOLERANCE, "min_return": config.MIN_EXPECTED_RETURN,
                  "max_legs": int(settings.get("max_tipps", 3)), "pool": optimizer.POOL_SIZE},
    }
    SITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SITE_PATH.write_text(site.render(data), encoding="utf-8")
    return data
