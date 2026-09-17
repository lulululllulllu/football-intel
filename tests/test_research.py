"""Tests der historischen Untersuchung mit SYNTHETISCHEN Spielen und eingebautem Fehler im Markt."""
import datetime as dt
import random
import unittest

from fi import db, research


def build(seed=4):
    """Liga, in der der Markt Unentschieden absichtlich um 6 Prozentpunkte unterschätzt."""
    rng = random.Random(seed)
    conn = db.connect(":memory:")
    teams = [f"T{i}" for i in range(10)]
    day = dt.date(2016, 8, 1)
    for season_start in range(2016, 2025):
        season = f"{season_start % 100:02d}{(season_start + 1) % 100:02d}"
        for _ in range(30):
            rng.shuffle(teams)
            for home, away in zip(teams[::2], teams[1::2]):
                p_h, p_d = 0.44, 0.30
                roll = rng.random()
                if roll < p_h:
                    hg, ag = 2, 1
                elif roll < p_h + p_d:
                    hg, ag = 1, 1
                else:
                    hg, ag = 0, 2
                mid = db.upsert_match(conn, {"competition": "D1", "season": season, "match_date": day.isoformat(),
                                             "home_team": home, "away_team": away, "home_goals": hg,
                                             "away_goals": ag, "home_shots_target": 5, "away_shots_target": 4},
                                      "football-data.co.uk", "t")
                market = {"H": 0.42, "D": 0.24, "A": 0.34}  # Markt unterschätzt Remis
                db.upsert_odds(conn, mid, [("bet365", "pre", "1x2", s, round(1 / (p * 1.05), 2))
                                           for s, p in market.items()], "t")
            day += dt.timedelta(days=7)
        day = dt.date(season_start + 1, 8, 1)
    return conn


class ResearchTests(unittest.TestCase):
    def test_detects_injected_draw_bias(self):
        report = research.run(build())
        draws = [f for f in report["factors"] if f["name"].startswith("Ausgeglichenes Spiel")][0]
        self.assertEqual(draws["verdict"], "BESTÄTIGT")
        self.assertGreater(draws["test"]["actual"], draws["test"]["market"])
        league_draw = [l for l in report["leagues"] if l["league"] == "Bundesliga" and l["selection"] == "D"][0]
        self.assertGreater(league_draw["test"]["return_single"], 1.0)

    def test_features_use_only_past(self):
        rows = research.load(build())
        research.add_features(rows)
        first = rows[0]
        self.assertIsNone(first.features["home"]["rest_days"])
        self.assertFalse(first.features["home"]["win_streak5"])
        # Team T? hat zu Beginn keine Historie, auch wenn es später Serien gibt
        self.assertTrue(all(r.features["home"]["remaining"] >= 0 for r in rows))

    def test_verdict_rules(self):
        strong = {"n": 500, "z": 2.5}
        self.assertEqual(research.verdict(strong, {"n": 200, "z": 1.8}), "BESTÄTIGT")
        self.assertEqual(research.verdict(strong, {"n": 200, "z": -1.8}), "nicht bestätigt")
        self.assertEqual(research.verdict({"n": 500, "z": 0.3}, {"n": 200, "z": 0.1}), "kein Effekt")
        self.assertEqual(research.verdict({"n": 50, "z": 3}, {"n": 200, "z": 2}), "zu wenig Spiele")


if __name__ == "__main__":
    unittest.main()
