"""Tests für The-Odds-API-Anbindung und Namenszuordnung. Alle Daten sind TESTDATEN."""
import datetime as dt
import unittest

from fi import db, odds_sync, teams
from fi.predict import market_probabilities
from fi.providers import odds_api


def event_with_odds(event_id, home, away, commence):
    return {"id": event_id, "sport_key": "soccer_germany_bundesliga", "commence_time": commence,
            "home_team": home, "away_team": away,
            "bookmakers": [
                {"key": "bet365", "title": "Bet365", "last_update": "2026-09-17T10:00:00Z", "markets": [
                    {"key": "h2h", "outcomes": [{"name": home, "price": 2.10}, {"name": away, "price": 3.20},
                                                {"name": "Draw", "price": 3.40}]},
                    {"key": "totals", "outcomes": [{"name": "Over", "price": 1.90, "point": 2.5},
                                                   {"name": "Under", "price": 1.90, "point": 2.5},
                                                   {"name": "Over", "price": 1.30, "point": 1.5}]}]},
                {"key": "betway", "title": "Betway", "markets": [
                    {"key": "h2h", "last_update": "2026-09-17T10:01:00Z",
                     "outcomes": [{"name": home, "price": 2.20}, {"name": away, "price": 3.10},
                                  {"name": "Draw", "price": 3.30}]}]}]}


class FakeApi:
    def __init__(self, events_by_key, remaining=400):
        self.events_by_key, self.remaining, self.used_last_call = events_by_key, remaining, 0
        self.odds_calls = []

    def events(self, key, until):
        return [{k: e[k] for k in ("id", "home_team", "away_team", "commence_time")}
                for e in self.events_by_key.get(key, [])]

    def odds(self, key, until, markets, bookmakers, regions=None):
        self.odds_calls.append((key, tuple(markets)))
        self.used_last_call = len(markets)
        return self.events_by_key.get(key, [])


class ParsingTests(unittest.TestCase):
    def test_parse_odds(self):
        quotes = odds_api.parse_odds(event_with_odds("a", "Team A", "Team B", "2026-09-19T13:30:00Z"))
        self.assertIn(("bet365", "1x2", "H", 2.10, "2026-09-17T10:00:00Z"), quotes)
        self.assertIn(("bet365", "1x2", "D", 3.40, "2026-09-17T10:00:00Z"), quotes)
        self.assertIn(("bet365", "ou25", "over", 1.90, "2026-09-17T10:00:00Z"), quotes)
        self.assertFalse(any(q[3] == 1.30 for q in quotes))  # Linie 1,5 gehört nicht zu Ü/U 2,5
        self.assertEqual(len([q for q in quotes if q[0] == "betway"]), 3)

    def test_uk_time(self):
        summer = odds_api.to_uk_time(dt.datetime(2026, 9, 19, 14, 30, tzinfo=dt.timezone.utc))
        winter = odds_api.to_uk_time(dt.datetime(2026, 12, 19, 14, 30, tzinfo=dt.timezone.utc))
        self.assertEqual((summer.hour, winter.hour), (15, 14))

    def test_find_sport_key_and_bookmakers(self):
        sports = [{"key": "soccer_germany_bundesliga", "title": "Bundesliga - Germany"},
                  {"key": "soccer_germany_bundesliga2", "title": "Bundesliga 2 - Germany"}]
        self.assertEqual(odds_api.find_sport_key(sports, "D1")[0], "soccer_germany_bundesliga")
        key, candidates = odds_api.find_sport_key(sports, "F1")
        self.assertIsNone(key)
        events = [event_with_odds("a", "A", "B", "2026-09-19T13:30:00Z")]
        events[0]["bookmakers"].append({"key": "unibet", "title": "Unibet", "markets": []})
        chosen = [k for k, _ in odds_api.choose_bookmakers(events)]
        self.assertEqual(chosen[:2], ["bet365", "betway"])

    def test_budget(self):
        today = dt.date(2026, 9, 16)  # 15 Tage bis Monatsende
        self.assertEqual(odds_sync.markets_for_budget(400, 5, today), ["h2h", "totals"])
        self.assertEqual(odds_sync.markets_for_budget(100, 5, today), ["h2h"])


class TeamMatchingTests(unittest.TestCase):
    def test_real_world_name_pairs(self):
        cases = [("Bayern Munich", ["Bayern Munich", "Dortmund"], "Bayern Munich"),
                 ("1. FC Köln", ["FC Koln", "Union Berlin"], "FC Koln"),
                 ("Borussia Monchengladbach", ["M'gladbach", "Dortmund"], "M'gladbach"),
                 ("Manchester City", ["Man United", "Man City"], "Man City"),
                 ("Atlético Madrid", ["Ath Madrid", "Real Madrid", "Ath Bilbao"], "Ath Madrid"),
                 ("Paris Saint Germain", ["Paris SG", "Paris FC"], "Paris SG"),
                 ("Wolverhampton Wanderers", ["Wolves", "West Ham"], "Wolves")]
        for name, candidates, expected in cases:
            self.assertEqual(teams.best_match(name, candidates)[0], expected, name)

    def test_ambiguous_stays_open(self):
        self.assertIsNone(teams.best_match("Athletic Club", ["Ath Madrid", "Ath Bilbao", "Real Madrid"])[0])


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        for home, away in (("Bayern Munich", "Dortmund"), ("FC Koln", "Leverkusen")):
            db.upsert_match(self.conn, {"competition": "D1", "season": "2627", "match_date": "2026-09-01",
                                        "home_team": home, "away_team": away,
                                        "home_goals": 1, "away_goals": 0}, "football-data.co.uk", "t")

    def test_sync_resolves_stores_and_saves_credits(self):
        future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        api = FakeApi({"soccer_germany_bundesliga": [
            event_with_odds("e1", "Bayern München", "Borussia Dortmund", future),
            event_with_odds("e2", "1. FC Köln", "Unbekannter Verein", future)]})
        summary = odds_sync.sync(self.conn, api, days=2)
        self.assertEqual(api.odds_calls, [("soccer_germany_bundesliga", ("h2h", "totals"))])  # nur Liga mit Spielen
        self.assertEqual(summary["leagues"]["D1"]["stored"], 1)
        self.assertEqual(summary["leagues"]["D1"]["unresolved"], ["Unbekannter Verein"])
        row = self.conn.execute("SELECT id FROM matches WHERE home_goals IS NULL").fetchone()
        mk = market_probabilities(self.conn, row["id"])
        self.assertEqual(mk["1x2"]["bookmaker"], "bet365/betway")
        self.assertEqual(mk["1x2"]["best"]["H"], (2.20, "betway"))
        self.assertAlmostEqual(sum(mk["1x2"]["probs"].values()), 1.0)

    def test_manual_alias_wins(self):
        teams.set_manual(self.conn, odds_sync.SOURCE, "FCB", "Bayern Munich")
        self.assertEqual(teams.resolve(self.conn, odds_sync.SOURCE, "FCB", "D1"), "Bayern Munich")


if __name__ == "__main__":
    unittest.main()
