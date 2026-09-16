"""Tests des Tormodells mit SYNTHETISCHEN Spielen (Zufallsdaten mit bekannten Stärken)."""
import datetime as dt
import math
import random
import unittest

from fi import db
from fi.models.goals import (GoalParams, MatchResult, fit, markets, probability,
                             score_matrix, tau, time_weight)
from fi.predict import market_probabilities, predict_upcoming


def _poisson(rng, lam):
    limit, k, prod = math.exp(-lam), 0, rng.random()
    while prod > limit:
        k += 1
        prod *= rng.random()
    return k


def synthetic_league(seed=1, rounds=4, start=dt.date(2025, 8, 1)):
    """Sechs Teams mit bekannter Stärke, jeder gegen jeden, mehrfach."""
    rng = random.Random(seed)
    strength = {"Stark": 1.6, "Gut": 1.25, "Mittel1": 1.0, "Mittel2": 1.0, "Schwach": 0.8, "Sehrschwach": 0.6}
    teams, matches, day = list(strength), [], start
    for _ in range(rounds):
        for home in teams:
            for away in teams:
                if home == away:
                    continue
                lh = 1.45 * 1.15 * strength[home] / strength[away]
                la = 1.45 * strength[away] / strength[home]
                matches.append(MatchResult(day, home, away, _poisson(rng, lh), _poisson(rng, la)))
                day += dt.timedelta(days=3)
    return matches, day


class MatrixTests(unittest.TestCase):
    def test_matrix_sums_to_one_and_markets_consistent(self):
        m = markets(score_matrix(1.6, 1.1, -0.08))
        self.assertAlmostEqual(sum(m["1x2"].values()), 1.0)
        self.assertAlmostEqual(m["ou25"]["over"] + m["ou25"]["under"], 1.0)
        self.assertAlmostEqual(m["btts"]["yes"] + m["btts"]["no"], 1.0)

    def test_pure_poisson_over25(self):
        lam = 2.7  # Summe zweier Poisson-Verteilungen ist wieder Poisson
        expected_under = sum(math.exp(-lam) * lam ** k / math.factorial(k) for k in range(3))
        m = score_matrix(1.5, 1.2, 0.0)
        self.assertAlmostEqual(probability(m, lambda h, a: h + a <= 2), expected_under, places=6)

    def test_tau_and_weight(self):
        self.assertAlmostEqual(tau(1, 1, 1.5, 1.0, -0.1), 1.1)
        self.assertEqual(tau(2, 1, 1.5, 1.0, -0.1), 1.0)
        self.assertAlmostEqual(time_weight(300, 300), 0.5)


class FitTests(unittest.TestCase):
    def test_recovers_strength_order(self):
        matches, end = synthetic_league(rounds=8)
        model = fit(matches, end, GoalParams(window_days=5000))
        self.assertGreater(model.attack["Stark"], model.attack["Mittel1"])
        self.assertGreater(model.attack["Mittel1"], model.attack["Sehrschwach"])
        self.assertLess(model.defence["Stark"], model.defence["Sehrschwach"])
        self.assertGreater(model.home_advantage, 1.0)
        lh, la = model.expected_goals("Stark", "Sehrschwach")
        self.assertGreater(lh, la)

    def test_no_lookahead(self):
        matches, _ = synthetic_league(rounds=2)
        cutoff = matches[40].match_date
        extreme_future = [MatchResult(cutoff, "Sehrschwach", "Stark", 9, 0)]
        a = fit(matches[:40], cutoff, GoalParams(window_days=5000))
        b = fit(matches[:40] + extreme_future, cutoff, GoalParams(window_days=5000))
        self.assertEqual(a.attack, b.attack)  # Spiel am Stichtag darf nicht einfließen

    def test_unknown_team_gives_none(self):
        matches, end = synthetic_league(rounds=2)
        self.assertIsNone(fit(matches, end).expected_goals("Stark", "Unbekannt"))
        self.assertIsNone(fit(matches[:10], end))  # zu wenig Spiele


class PipelineTests(unittest.TestCase):
    def test_upsert_never_erases_result(self):
        conn = db.connect(":memory:")
        base = {"competition": "D1", "season": "2627", "match_date": "2026-09-19",
                "home_team": "A", "away_team": "B"}
        db.upsert_match(conn, {**base, "home_goals": 2, "away_goals": 1}, "s", "t1")
        db.upsert_match(conn, {**base, "home_goals": None, "away_goals": None}, "s", "t2")
        row = conn.execute("SELECT home_goals, away_goals FROM matches").fetchone()
        self.assertEqual(tuple(row), (2, 1))

    def test_margin_removed(self):
        conn = db.connect(":memory:")
        mid = db.upsert_match(conn, {"competition": "D1", "season": "2627", "match_date": "2026-09-19",
                                     "home_team": "A", "away_team": "B"}, "s", "t")
        db.upsert_odds(conn, mid, [("bet365", "pre", "1x2", "H", 2.0), ("bet365", "pre", "1x2", "D", 3.4),
                                   ("bet365", "pre", "1x2", "A", 3.6)], "t")
        mk = market_probabilities(conn, mid)["1x2"]
        self.assertAlmostEqual(sum(mk["probs"].values()), 1.0)
        self.assertLess(mk["probs"]["H"], 0.5)   # 2.00 ist nach Margenabzug weniger als 50 %
        self.assertGreater(mk["margin"], 0)

    def test_predict_end_to_end(self):
        conn = db.connect(":memory:")
        matches, end = synthetic_league(rounds=6)
        for m in matches:
            db.upsert_match(conn, {"competition": "D1", "season": "2526", "match_date": m.match_date.isoformat(),
                                   "home_team": m.home, "away_team": m.away,
                                   "home_goals": m.home_goals, "away_goals": m.away_goals}, "s", "t")
        db.upsert_match(conn, {"competition": "D1", "season": "2526", "match_date": end.isoformat(),
                               "home_team": "Stark", "away_team": "Neu"}, "s", "t")
        db.upsert_match(conn, {"competition": "D1", "season": "2526", "match_date": end.isoformat(),
                               "home_team": "Stark", "away_team": "Sehrschwach"}, "s", "t")
        items = predict_upcoming(conn, end, 3, ["D1"])
        by_away = {i["match"]["away_team"]: i for i in items}
        self.assertIn("error", by_away["Neu"])
        self.assertGreater(by_away["Sehrschwach"]["model"]["1x2"]["H"], 0.5)


if __name__ == "__main__":
    unittest.main()
