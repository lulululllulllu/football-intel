"""Kommandozeile. Aufruf: python -m fi <befehl>"""
from __future__ import annotations

import argparse
import dataclasses
import itertools
import json
import datetime as dt
import os

from fi import config, db
from fi.models.elo import compute_all
from fi import backtest, odds_sync, optimizer, teams
from fi.providers import odds_api
from fi.models.goals import GoalParams
from fi.predict import MODEL_VERSION, predict_upcoming, store_predictions
from fi.providers import clubelo
from fi.providers import football_data_uk as fduk
from fi.timeutil import berlin_label
from fi.providers.api_football import ApiError, ApiFootball, QuotaExceeded


def cmd_update(args) -> int:
    conn = db.connect()
    today = dt.date.today()
    current = config.season_start_for(today)
    total = 0
    for code in args.league or list(config.LEAGUES):
        for start in config.seasons(args.from_season, today):
            label = f"{config.LEAGUES[code]['name']} {config.season_code(start)}"
            try:
                raw = fduk.download(code, start, force=(start == current))
            except fduk.NotAvailable:
                print(f"  {label}: keine Datei verfügbar")
                continue
            except Exception as exc:  # Netzwerkfehler melden, aber mit den anderen Ligen weitermachen
                print(f"  {label}: FEHLER beim Download ({exc})")
                continue
            matches = fduk.parse_results_csv(raw, code, start)
            fetched_at = db.utc_now()
            with conn:
                for match in matches:
                    match_id = db.upsert_match(conn, match, fduk.SOURCE, fetched_at)
                    db.upsert_odds(conn, match_id, match["odds"], fetched_at)
            total += len(matches)
            print(f"  {label}: {len(matches)} Spiele")
    print(f"Fertig: {total} Spiele verarbeitet.")

    print("Lade kommende Spiele und aktuelle Quoten ...")
    try:
        fixtures = fduk.parse_fixtures_csv(fduk.download_fixtures(), args.league or list(config.LEAGUES))
    except Exception as exc:
        print(f"  Spielplan nicht verfügbar ({exc}). Ergebnisse sind trotzdem gespeichert.")
        return 0
    fetched_at = db.utc_now()
    with conn:
        for match in fixtures:
            match_id = db.upsert_match(conn, match, fduk.SOURCE, fetched_at)
            db.upsert_odds(conn, match_id, match["odds"], fetched_at)
    print(f"  {len(fixtures)} kommende Spiele gespeichert.")
    return 0


WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def _pct(value: float | None) -> str:
    return f"{value * 100:4.0f}%" if value is not None else "   – "


def cmd_predict(args) -> int:
    conn = db.connect()
    today = dt.date.today()
    items = predict_upcoming(conn, today, args.days, args.league or list(config.LEAGUES))
    if not items:
        print("Keine kommenden Spiele gefunden. Zuerst `python -m fi update` ausführen.")
        return 0
    print("HINWEIS: Modell noch nicht per Backtest geprüft. Keine Grundlage für echte Wetten.\n")
    for item in items:
        m = item["match"]
        print(f"{berlin_label(m['match_date'], m['kick_time'])} Uhr  "
              f"{item['league']}: {m['home_team']} – {m['away_team']}")
        if "error" in item:
            print(f"    {item['error']}\n")
            continue
        model, market = item["model"], item["market"]
        mk = {k: market.get(k, {}).get("probs", {}) for k in ("1x2", "ou25", "btts")}
        print(f"    Erwartete Tore   {item['expected_goals'][0]:.2f} : {item['expected_goals'][1]:.2f}")
        print(f"    1 / X / 2        Modell {_pct(model['1x2']['H'])} {_pct(model['1x2']['D'])} "
              f"{_pct(model['1x2']['A'])}   Markt {_pct(mk['1x2'].get('H'))} {_pct(mk['1x2'].get('D'))} "
              f"{_pct(mk['1x2'].get('A'))}")
        print(f"    Über 2,5 Tore    Modell {_pct(model['ou25']['over'])}"
              f"               Markt {_pct(mk['ou25'].get('over'))}")
        print(f"    Beide treffen    Modell {_pct(model['btts']['yes'])}"
              f"               Markt {_pct(mk['btts'].get('yes'))}")
        source = market.get("1x2")
        if source:
            best = "  ".join(f"{label} {source['best'][sel][0]:.2f} ({source['best'][sel][1]})"
                             for label, sel in (("1", "H"), ("X", "D"), ("2", "A")))
            stamp = f", Stand {source['fetched_at'][:16].replace('T', ' ')} UTC" if source["fetched_at"] else ""
            print(f"    Beste Quoten     {best}")
            print(f"    Quellen: {source['bookmaker']}, Marge {source['margin'] * 100:.1f}%{stamp}")
        else:
            print("    Keine Quoten verfügbar")
        low = [n for n in item["effective_games"] if n < config.LOW_DATA_THRESHOLD]
        if low:
            print("    Achtung: wenig Spieldaten für mindestens ein Team")
        print()
    stored = store_predictions(conn, items)
    print(f"{stored} Einzelprognosen mit Zeitstempel gespeichert.")
    return 0


MARKET_LABELS = {"1x2": "1 / X / 2", "ou25": "Über/Unter 2,5"}


def _goal_params(args, **overrides) -> GoalParams:
    values = dataclasses.asdict(GoalParams.from_config())
    for name, arg in (("half_life_days", args.half_life), ("prior_matches", args.prior),
                      ("elo_prior_beta", args.beta)):
        if arg is not None:
            values[name] = arg
    values.update(overrides)
    return GoalParams(**values)


def _save_run(conn, leagues, from_season, params: GoalParams, results: dict) -> None:
    with conn:
        conn.execute("INSERT INTO backtest_runs (run_at, model_version, leagues, from_season, params, results) "
                     "VALUES (?, ?, ?, ?, ?, ?)",
                     (db.utc_now(), MODEL_VERSION, ",".join(leagues), from_season,
                      json.dumps(dataclasses.asdict(params)), json.dumps(results)))


def _print_comparison(name: str, cmp: dict | None) -> None:
    if not cmp:
        print(f"{name}: keine Spiele mit Vorab- und Schlussquoten\n")
        return
    print(f"{name}  ({cmp['n']} Spiele)")
    print(f"                    Log Loss   Brier")
    print(f"  Modell            {cmp['model'][0]:.4f}    {cmp['model'][1]:.4f}")
    print(f"  Markt vorab       {cmp['pre'][0]:.4f}    {cmp['pre'][1]:.4f}")
    print(f"  Markt Schluss     {cmp['close'][0]:.4f}    {cmp['close'][1]:.4f}")
    best = min(cmp["blends"], key=cmp["blends"].get)
    line = "  ".join(f"{int(w * 100)}%: {v:.4f}" for w, v in cmp["blends"].items())
    print(f"  Mischung (Anteil Modell):  {line}")
    gain = cmp["blends"][0.0] - cmp["blends"][best]
    if best == 0.0:
        print("  -> Das Modell verbessert die Vorab-Quoten nicht.")
    elif gain < 0.001:
        print(f"  -> Beste Mischung mit {int(best * 100)}% Modell, aber Verbesserung nur {gain:.4f}: "
              "praktisch kein Mehrwert (Zufallsbereich).")
    else:
        print(f"  -> Beste Mischung mit {int(best * 100)}% Modell, Verbesserung {gain:.4f}")
    print()


def cmd_backtest(args) -> int:
    conn = db.connect()
    leagues = args.league or list(config.LEAGUES)
    if not conn.execute("SELECT 1 FROM matches LIMIT 1").fetchone():
        print("Keine Daten. Zuerst `python -m fi update` ausführen.")
        return 1

    if args.grid:
        grid = list(itertools.product((200.0, 300.0, 450.0), (3.0, 8.0, 15.0), (0.6, 1.0)))
        print(f"Teste {len(grid)} Einstellungen. Das kann eine Weile dauern.\n")
        summary = []
        for half_life, prior, beta in grid:
            params = _goal_params(args, half_life_days=half_life, prior_matches=prior, elo_prior_beta=beta)
            records, _ = backtest.run(conn, leagues, args.from_season, params, verbose=False)
            results = {m: backtest.compare(records, m) for m in backtest.EVAL_MARKETS}
            _save_run(conn, leagues, args.from_season, params, results)
            row = (half_life, prior, beta, results["1x2"]["model"][0] if results["1x2"] else float("nan"),
                   results["ou25"]["model"][0] if results["ou25"] else float("nan"))
            summary.append(row)
            print(f"  Halbwertszeit {half_life:>5.0f}  Prior {prior:>4.0f}  Beta {beta:.1f}  "
                  f"->  Log Loss 1X2 {row[3]:.4f}   Ü/U 2,5 {row[4]:.4f}")
        best = min(summary, key=lambda r: r[3] + r[4])
        print(f"\nBeste Einstellung: Halbwertszeit {best[0]:.0f}, Prior {best[1]:.0f}, Beta {best[2]:.1f}")
        print("Übernehmen: in fi/config.py unter GOALS eintragen.")
        return 0

    params = _goal_params(args)
    print(f"Backtest ab Saison {args.from_season}/{(args.from_season + 1) % 100:02d}\n"
          f"Einstellungen: Halbwertszeit {params.half_life_days:.0f} Tage, Prior {params.prior_matches:.0f}, "
          f"Beta {params.elo_prior_beta}\n")
    records, skipped = backtest.run(conn, leagues, args.from_season, params)
    print(f"\nGesamt: {len(records)} Spiele bewertet, {skipped} übersprungen (zu wenig Daten).\n")

    results = {}
    for market in backtest.EVAL_MARKETS:
        results[market] = backtest.compare(records, market)
        _print_comparison(MARKET_LABELS[market], results[market])

    print("Nach Datenlage (1 / X / 2, Log Loss)")
    for label, subset in (("wenig Daten", [r for r in records if r.low_data]),
                          ("genug Daten", [r for r in records if not r.low_data])):
        cmp = backtest.compare(subset, "1x2")
        if cmp:
            print(f"  {label:<12} {cmp['n']:>6} Spiele   Modell {cmp['model'][0]:.4f}   "
                  f"Markt vorab {cmp['pre'][0]:.4f}")
    print()

    print("Nach Liga (1 / X / 2, Log Loss)")
    for code in leagues:
        cmp = backtest.compare([r for r in records if r.competition == code], "1x2")
        if cmp:
            print(f"  {config.LEAGUES[code]['name']:<15} {cmp['n']:>5} Spiele   Modell {cmp['model'][0]:.4f}   "
                  f"Markt vorab {cmp['pre'][0]:.4f}   Schluss {cmp['close'][0]:.4f}")
    print()

    print("Kalibrierung 1 / X / 2: vorhergesagt vs. tatsächlich eingetreten")
    print("  Bereich      Modell: Anzahl  vorhergesagt  eingetreten")
    for low, high, count, predicted, actual in backtest.calibration(records, "model", "1x2"):
        print(f"  {low * 100:>3.0f}–{high * 100:<3.0f}%          {count:>6}      {predicted * 100:5.1f}%      "
              f"{actual * 100:5.1f}%")
    _save_run(conn, leagues, args.from_season, params, results)
    print("\nErgebnis gespeichert.")
    return 0


def _odds_api() -> "odds_api.OddsApi | None":
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        print("Kein Schlüssel gefunden. Im Terminal setzen mit:\n"
              "  $env:ODDS_API_KEY = \"dein-schluessel\"")
        return None
    return odds_api.OddsApi(key, db.connect())


def cmd_oddscheck(args) -> int:
    api = _odds_api()
    if not api:
        return 1
    conn = api.conn
    names = {**config.LEAGUES, **config.CUPS}
    try:
        sports = api.sports()  # kostenlos
    except Exception as exc:
        print(f"Fehler bei der Anfrage: {exc}")
        if "401" in str(exc):
            print("Der Schlüssel wird nicht akzeptiert. Prüfe, ob es der Schlüssel von the-odds-api.com ist.")
        return 1
    print(f"Verbindung OK. Restguthaben diesen Monat: {api.remaining} Credits.\n")
    print("Wettbewerbe:")
    missing = 0
    for code in names:
        stored = odds_sync._setting(conn, code)
        if stored and stored in {x.get("key") for x in sports}:
            print(f"  OK     {names[code]['name']:<24} {stored}")
            continue
        key, candidates = odds_api.find_sport_key(sports, code)
        if key:
            print(f"  OK     {names[code]['name']:<24} {key}")
        else:
            missing += 1
            print(f"  OFFEN  {names[code]['name']:<24} Kandidaten: "
                  + (", ".join(f"{c.get('title')} [{c.get('key')}]" for c in candidates) or "keine"))
    if missing:
        print("  Festlegen mit: python -m fi setleague CODE kennung")

    if args.no_bookmakers:
        return 0
    print("\nBuchmacher werden ermittelt (kostet 2 Credits) ...")
    until = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=7)
    for code in config.LEAGUES:
        try:
            events = api.odds(odds_sync.league_key(conn, code), until, ["h2h"], None, regions="uk,eu")
        except Exception:
            continue
        if events:
            break
    else:
        print("Keine Spiele mit Quoten gefunden. Später erneut versuchen.")
        return 1
    chosen = odds_api.choose_bookmakers(events)
    odds_sync.set_bookmakers(conn, [k for k, _ in chosen])
    print("Ausgewählte Buchmacher (bis zu 10 kosten dasselbe wie einer):")
    for key, title in chosen:
        print(f"  {title:<22} [{key}]")
    for wanted in config.ODDS_API_PREFERRED_BOOKMAKERS:
        if wanted not in {k for k, _ in chosen}:
            print(f"  Hinweis: {wanted} ist bei diesem Anbieter derzeit nicht verfügbar.")
    print(f"\nRestguthaben: {api.remaining} Credits.")
    return 0


def cmd_setleague(args) -> int:
    odds_sync.set_league_key(db.connect(), args.code, args.key)
    print(f"{args.code} -> {args.key} gespeichert.")
    return 0


def cmd_odds(args) -> int:
    api = _odds_api()
    if not api:
        return 1
    try:
        summary = odds_sync.sync(api.conn, api, args.days)
    except Exception as exc:
        print(f"Fehler bei der Anfrage: {exc}")
        if "401" in str(exc):
            print("Der Schlüssel wird nicht akzeptiert. Prüfe, ob es der Schlüssel von the-odds-api.com ist.")
        return 1
    names = {**config.LEAGUES, **config.CUPS}
    for code, info in summary["leagues"].items():
        if not info["events"]:
            continue
        print(f"  {names[code]['name']:<24} {info['events']:>3} Spiele, {info['stored']:>3} gespeichert")
        for unresolved in sorted(set(info["unresolved"])):
            print(f"      Team nicht zugeordnet: {unresolved}")
    if summary["markets"] == ["h2h"]:
        print("  Sparmodus: nur Siegwette geladen, damit das Guthaben bis Monatsende reicht.")
    print(f"Quoten für {summary['odds_matches']} Spiele gespeichert. "
          f"Verbraucht: {summary['credits_used']} Credits, Rest diesen Monat: {api.remaining}.")
    if any(info["unresolved"] for info in summary["leagues"].values()):
        print('Zuordnen mit:  python -m fi alias "Name beim Anbieter" "Name aus python -m fi ratings"')
    return 0


def cmd_books(args) -> int:
    conn = db.connect()
    if args.keys:
        optimizer.set_my_bookmakers(conn, [k.lower() for k in args.keys])
    known = {r[0] for r in conn.execute("SELECT DISTINCT bookmaker FROM odds WHERE timing='current'")}
    mine = optimizer.my_bookmakers(conn)
    print(f"Deine Buchmacher: {', '.join(mine)}")
    unknown = [k for k in mine if known and k not in known]
    if unknown:
        print(f"Achtung, dafür liegen keine Quoten vor: {', '.join(unknown)}")
        print(f"Verfügbar: {', '.join(sorted(known))}")
    return 0


def cmd_bet(args) -> int:
    conn = db.connect()
    allowed = optimizer.my_bookmakers(conn)
    now = dt.datetime.now(dt.timezone.utc)
    legs, notes = optimizer.collect_legs(conn, now, args.days, allowed)
    names = {**config.LEAGUES, **config.CUPS}
    print(f"Zielquote {args.target:.2f} (erlaubt {args.target * (1 - config.TARGET_TOLERANCE):.2f} bis "
          f"{args.target * (1 + config.TARGET_TOLERANCE):.2f}), höchstens {args.max_legs} Tipps, "
          f"Buchmacher: {', '.join(allowed)}")
    print(f"{len(legs)} mögliche Einzeltipps mit frischen Quoten gefunden.\n")
    if not legs:
        print("Keine Wette möglich: keine frischen Quoten. Zuerst `python -m fi odds` ausführen.")
        return 0

    suggestions = optimizer.optimize(legs, args.target, args.max_legs)
    good = [s for s in suggestions if s.expected_return >= config.MIN_EXPECTED_RETURN]
    if not good:
        print("HEUTE KEINE WETTE.")
        if suggestions:
            best = suggestions[0]
            print(f"Die beste Möglichkeit hätte eine erwartete Rückzahlung von nur {best.expected_return:.0%} "
                  f"des Einsatzes. Grenze: {config.MIN_EXPECTED_RETURN:.0%}.")
        else:
            print("Keine Kombination erreicht die Zielquote. Andere Zielquote oder mehr Tipps versuchen.")
        return 0

    for number, s in enumerate(good, 1):
        print(f"VORSCHLAG {number}:  Quote {s.total_odds:.2f}   Gewinnchance {s.win_probability:.1%}   "
              f"erwartete Rückzahlung {s.expected_return:.0%} des Einsatzes")
        for leg in s.legs:
            model = f"Modell {leg.p_model:.0%}" if leg.p_model is not None else "kein Modell"
            print(f"   {leg.kickoff_local} Uhr  {names[leg.competition]['name']}: {leg.home} – {leg.away}")
            print(f"      {leg.label:<28} Quote {leg.price:.2f} bei {leg.bookmaker}   "
                  f"Markt {leg.p_market:.0%} ({leg.n_bookmakers} Buchmacher), {model}")
        if s.expected_return > 1.0:
            print("   Über 100 % ist fast immer eine Ausreißer- oder veraltete Quote, kein sicherer Vorteil. "
                  "Quote beim Buchmacher prüfen.")
        fair = 1 / s.win_probability
        print(f"   Faire Quote wäre {fair:.2f}. Die Differenz ist die Marge, die du zahlst.")
        if len(s.legs) > 1:
            print("   Jeder Tipp muss aufgehen. Ein einzelner Fehlschlag verliert die ganze Wette.")
        print()
    optimizer.save(conn, args.target, good[0])
    for note in notes[:5]:
        print(note)
    print("\nHinweise: Aufstellungen und Verletzungen sind nicht geprüft. Quoten kurz vor dem Wetten "
          "beim Buchmacher kontrollieren. Erwartete Rückzahlung unter 100 % heißt: langfristig Verlust.")
    return 0


def cmd_website(args) -> int:
    from fi import cloud
    data = cloud.run(fetch_data=not args.no_update, fetch_odds=not args.no_odds)
    print(f"Website erstellt: {cloud.SITE_PATH}")
    print(f"{len(data['matches'])} Spiele, {len(data['legs'])} mögliche Tipps, "
          f"{sum(1 for t in data['targets'] if t['suggestion'])} von {len(data['targets'])} Zielquoten mit Empfehlung.")
    if data["odds_error"]:
        print(f"Hinweis: {data['odds_error']}")
    return 0


def cmd_alias(args) -> int:
    conn = db.connect()
    if args.source_name and args.canonical:
        if args.canonical not in teams.current_teams(conn, None):
            print(f'"{args.canonical}" gibt es in der Datenbank nicht. Schreibweise wie bei `ratings` verwenden.')
            return 1
        teams.set_manual(conn, odds_sync.SOURCE, args.source_name, args.canonical)
        print(f'"{args.source_name}" -> "{args.canonical}" gespeichert.')
        return 0
    rows = conn.execute("SELECT source_name, canonical, method, score FROM team_name_map "
                        "ORDER BY method DESC, source_name").fetchall()
    for r in rows:
        score = f"{r['score']:.2f}" if r["score"] is not None else "   "
        print(f"  {r['method']:<8} {score}  {r['source_name']:<30} -> {r['canonical']}")
    print(f"{len(rows)} Zuordnungen. Falsche korrigieren mit: python -m fi alias \"Quellname\" \"DB-Name\"")
    return 0


def cmd_daily(args) -> int:
    print("1/3  Ergebnisse und Spielplan laden")
    cmd_update(argparse.Namespace(league=None, from_season=config.season_start_for(dt.date.today()) - 1))
    print("\n2/3  Aktuelle Quoten laden")
    if cmd_odds(argparse.Namespace(days=args.days)) != 0:
        print("Weiter ohne aktuelle Quoten.")
    print("\n3/3  Prognosen")
    return cmd_predict(argparse.Namespace(league=None, days=args.days))


def cmd_elo(args) -> int:
    n = compute_all(db.connect())
    print(f"Elo neu berechnet über {n} Spiele ({config.ELO_MODEL_VERSION}).")
    return 0


def cmd_ratings(args) -> int:
    conn = db.connect()
    season = conn.execute("SELECT MAX(season) AS s FROM team_ratings WHERE competition=?",
                          (args.league,)).fetchone()["s"]
    if season is None:
        print("Keine Ratings vorhanden. Zuerst `update` und `elo` ausführen.")
        return 1
    rows = conn.execute(
        "SELECT team, rating, last_match_date FROM team_ratings "
        "WHERE competition=? AND season=? ORDER BY rating DESC", (args.league, season)).fetchall()
    print(f"{config.LEAGUES[args.league]['name']} {season} (Stand: letztes Spiel je Team)")
    for i, r in enumerate(rows, 1):
        print(f"{i:>3}. {r['team']:<25} {r['rating']:7.1f}   {r['last_match_date']}")
    return 0


def cmd_status(args) -> int:
    conn = db.connect()
    rows = conn.execute(
        "SELECT competition, season, COUNT(*) AS n, MAX(match_date) AS last, "
        "(SELECT COUNT(*) FROM odds o JOIN matches m2 ON m2.id = o.match_id "
        " WHERE m2.competition = m.competition AND m2.season = m.season) AS odds "
        "FROM matches m GROUP BY competition, season ORDER BY competition, season").fetchall()
    if not rows:
        print("Datenbank ist leer. Starte mit: python -m fi update")
        return 0
    for r in rows:
        print(f"{r['competition']:<4} {r['season']}  {r['n']:>4} Spiele  {r['odds']:>6} Quoten  bis {r['last']}")
    return 0


def cmd_clubelo(args) -> int:
    conn = db.connect()
    today = dt.date.today()
    ratings = clubelo.parse_ratings_csv(clubelo.fetch_ratings_csv(today))
    fetched_at = db.utc_now()
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO clubelo_ratings VALUES (?, ?, ?, ?, ?, ?)",
            [(today.isoformat(), r["club"], r["country"], r["level"], r["elo"], fetched_at)
             for r in ratings])
    countries = {league["country"] for league in config.LEAGUES.values()}
    top = [r for r in ratings if r["country"] in countries and r["level"] == 1]
    top.sort(key=lambda r: r["elo"], reverse=True)
    print(f"{len(ratings)} ClubElo-Werte gespeichert. Top 15 der konfigurierten Ligen:")
    for i, r in enumerate(top[:15], 1):
        print(f"{i:>3}. {r['club']:<22} {r['country']}  {r['elo']:7.1f}")
    return 0


def cmd_apicheck(args) -> int:
    key = os.environ.get("APIFOOTBALL_KEY")
    if not key:
        print("Kein Schlüssel gefunden. Im Terminal setzen mit:\n"
              "  export APIFOOTBALL_KEY='dein-schluessel'")
        return 1
    api = ApiFootball(key, db.connect())
    try:
        status = api.status()
        print("Konto:", status.get("subscription", {}), "| Anfragen:", status.get("requests", {}))
        fixtures = api.fixtures_on(dt.date.today())
    except QuotaExceeded as exc:
        print(f"Tageskontingent erreicht: {exc}")
        return 1
    except ApiError as exc:
        print(f"API meldet Fehler (evtl. Einschränkung im Gratis-Tarif): {exc}")
        return 1
    print(f"Heute {len(fixtures)} Spiele in den konfigurierten Wettbewerben:")
    for f in fixtures:
        print(f"  {f['kickoff'][11:16]}  {f['league']:<24} {f['home_team']} – {f['away_team']}  "
              f"[Saison {f['season']}, Status {f['status']}]")
    print(f"Heute verbraucht: {api.used_today()} Anfragen.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m fi", description="Football Intelligence")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("update", help="Historische Ergebnisse und Quoten laden")
    p.add_argument("--league", action="append", choices=list(config.LEAGUES))
    p.add_argument("--from-season", type=int, default=config.FIRST_SEASON)
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("predict", help="Prognosen für kommende Spiele (Modell gegen Markt)")
    p.add_argument("--league", action="append", choices=list(config.LEAGUES))
    p.add_argument("--days", type=int, default=7)
    p.set_defaults(func=cmd_predict)

    p = sub.add_parser("backtest", help="Modell an vergangenen Spielen prüfen")
    p.add_argument("--league", action="append", choices=list(config.LEAGUES))
    p.add_argument("--from-season", type=int, default=2019)
    p.add_argument("--half-life", type=float)
    p.add_argument("--prior", type=float)
    p.add_argument("--beta", type=float)
    p.add_argument("--grid", action="store_true", help="18 Einstellungen vergleichen")
    p.set_defaults(func=cmd_backtest)

    p = sub.add_parser("oddscheck", help="Quotenanbieter prüfen, Buchmacher auswählen (2 Credits)")
    p.add_argument("--no-bookmakers", action="store_true", help="nur kostenlos prüfen")
    p.set_defaults(func=cmd_oddscheck)

    p = sub.add_parser("setleague", help="Wettbewerbs-Kennung beim Quotenanbieter festlegen")
    p.add_argument("code", choices=[*config.LEAGUES, *config.CUPS])
    p.add_argument("key")
    p.set_defaults(func=cmd_setleague)

    p = sub.add_parser("odds", help="Kommende Spiele und aktuelle Quoten laden")
    p.add_argument("--days", type=int, default=2)
    p.set_defaults(func=cmd_odds)

    p = sub.add_parser("alias", help="Teamnamen-Zuordnungen anzeigen oder festlegen")
    p.add_argument("source_name", nargs="?")
    p.add_argument("canonical", nargs="?")
    p.set_defaults(func=cmd_alias)

    p = sub.add_parser("bet", help="Wette mit der höchsten Gewinnchance für eine Zielquote suchen")
    p.add_argument("--target", type=float, required=True, help="z.B. 3.0")
    p.add_argument("--max-legs", type=int, default=3)
    p.add_argument("--days", type=int, default=2)
    p.set_defaults(func=cmd_bet)

    p = sub.add_parser("books", help="Eigene Buchmacher anzeigen oder festlegen, z.B. books skybet betway")
    p.add_argument("keys", nargs="*")
    p.set_defaults(func=cmd_books)

    p = sub.add_parser("website", help="Tageslauf ausführen und Website docs/index.html erzeugen")
    p.add_argument("--no-update", action="store_true", help="keine Ergebnisse neu laden")
    p.add_argument("--no-odds", action="store_true", help="keine Quoten abrufen (spart Credits)")
    p.set_defaults(func=cmd_website)

    p = sub.add_parser("daily", help="Täglicher Lauf: Daten, Quoten, Prognosen")
    p.add_argument("--days", type=int, default=2)
    p.set_defaults(func=cmd_daily)

    sub.add_parser("elo", help="Elo-Ratings neu berechnen").set_defaults(func=cmd_elo)

    p = sub.add_parser("ratings", help="Aktuelle Elo-Tabelle einer Liga anzeigen")
    p.add_argument("--league", default="D1", choices=list(config.LEAGUES))
    p.set_defaults(func=cmd_ratings)

    sub.add_parser("status", help="Datenbestand anzeigen").set_defaults(func=cmd_status)
    sub.add_parser("clubelo", help="Ligaübergreifende ClubElo-Werte laden").set_defaults(func=cmd_clubelo)
    sub.add_parser("apicheck", help="API-Football-Zugang testen (2 Anfragen)").set_defaults(func=cmd_apicheck)

    args = parser.parse_args(argv)
    return args.func(args)
