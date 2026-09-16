# Football Intelligence – Schritt 5: Optimierer

Persönliches Analyse-Projekt. Keine externen Python-Pakete nötig (nur Python 3.10+).

## Einrichtung auf dem Mac

```bash
cd football_intel
python3 --version          # mindestens 3.10
python3 -m unittest discover -v   # alle Tests sollten "OK" melden
```

## Befehle

| Befehl | Was passiert |
|---|---|
| `python3 -m fi update` | Lädt Ergebnisse, Schüsse und Quoten aller 8 Ligen ab 2015/16. Erster Lauf dauert einige Minuten, danach wird nur die laufende Saison neu geladen. |
| `python3 -m fi update --league D1 --from-season 2022` | Nur eine Liga / ab einer Saison |
| `python3 -m fi status` | Zeigt, welche Daten in der Datenbank sind |
| `python3 -m fi predict` | Prognosen für die nächsten 7 Tage: Modell gegen Markt (`--days 3`, `--league E0`) |
| `python3 -m fi backtest` | Prüft das Modell an allen Spielen ab 2019/20 gegen Ergebnisse und Quoten |
| `python3 -m fi backtest --grid` | Vergleicht 18 Modelleinstellungen (dauert einige Minuten) |
| `python3 -m fi oddscheck` | Quotenanbieter the-odds-api.com prüfen, Buchmacher auswählen |
| `python3 -m fi odds` | Kommende Spiele (2 Tage) und aktuelle Quoten laden, sparsam mit den 500 Gratis-Credits |
| `python3 -m fi alias` | Teamnamen-Zuordnungen anzeigen; `alias "Quellname" "DB-Name"` legt eine fest |
| `python3 -m fi bet --target 3` | Wette mit der höchsten echten Gewinnchance für Zielquote 3.00 |
| `python3 -m fi books skybet betway` | Eigene Buchmacher festlegen (nur deren Quoten werden empfohlen) |
| `python3 -m fi daily` | Täglicher Lauf: Ergebnisse, Quoten, Prognosen |
| `python3 -m fi elo` | Berechnet alle Elo-Ratings chronologisch neu |
| `python3 -m fi ratings --league E0` | Aktuelle Elo-Tabelle einer Liga |
| `python3 -m fi clubelo` | Lädt ligaübergreifende ClubElo-Werte von heute |
| `python3 -m fi apicheck` | Testet den API-Football-Zugang (verbraucht 2 von 100 Anfragen) |

Liga-Codes: E0 Premier League, D1 Bundesliga, SP1 La Liga, I1 Serie A,
F1 Ligue 1, N1 Eredivisie, P1 Primeira Liga, B1 Pro League.

## API-Football einrichten

1. Kostenloses Konto auf api-football.com anlegen, Schlüssel kopieren.
2. Im Terminal: `export APIFOOTBALL_KEY='dein-schluessel'`
3. `python3 -m fi apicheck`

Der Schlüssel gehört nie in den Code. Das Programm hält automatisch 5 Anfragen pro Tag in Reserve.

## Aufbau

```
fi/config.py                  Ligen, Pfade, Modellparameter (nur hier ändern)
fi/db.py                      SQLite-Schema und Speichern
fi/providers/football_data_uk.py   historische Ergebnisse + Quoten
fi/providers/clubelo.py       ligaübergreifende Stärke
fi/providers/api_football.py  Spielpläne, Aufstellungen, Verletzungen
fi/models/elo.py              eigenes Elo-Modell
fi/models/goals.py            Dixon-Coles-Tormodell (1X2, Über/Unter, beide treffen)
fi/predict.py                 Prognosen für kommende Spiele + Marktvergleich
fi/backtest.py                Backtest ohne Lookahead, Log Loss, Brier, Kalibrierung
tests/                        Tests mit klar markierten Testdaten
data/                         wird automatisch angelegt (Rohdateien + football.db)
```

## Fehlerbehebung

- **`SSL: CERTIFICATE_VERIFY_FAILED`**: Bei Python von python.org einmal
  „Install Certificates.command“ im Ordner `/Applications/Python 3.x/` ausführen.
- **Liga/Saison „keine Datei verfügbar“**: Saison noch nicht gestartet oder vom Anbieter nicht angeboten.

## Windows

Überall `python` statt `python3` verwenden. Vor den Befehlen einmal `$env:PYTHONUTF8 = "1"` eingeben.

Schlüssel von the-odds-api.com (PowerShell): `$env:ODDS_API_KEY = "dein-schluessel"`

## Online-Betrieb (GitHub)

`.github/workflows/tageslauf.yml` startet täglich um 09:00 UTC `python -m fi website`.
Das Ergebnis landet in `docs/index.html` (GitHub Pages), die Bilanz in `state/recommendations.json`.
Einstellungen wie Buchmacher und Zielquoten stehen in `settings.json` und lassen sich direkt auf GitHub bearbeiten.
Der Schlüssel von the-odds-api.com gehört als Repository-Secret `ODDS_API_KEY` hinterlegt, nie in eine Datei.
