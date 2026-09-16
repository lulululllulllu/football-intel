import unittest

from fi import db
from fi.models.elo import (EloEngine, EloParams, compute_all, expected_home_score,
                           goal_multiplier)


class EloFormulaTests(unittest.TestCase):
    def test_expected_score(self):
        self.assertAlmostEqual(expected_home_score(1500, 1500, 0), 0.5)
        self.assertGreater(expected_home_score(1500, 1500, 65), 0.5)

    def test_goal_multiplier(self):
        self.assertEqual(goal_multiplier(1), 1.0)
        self.assertEqual(goal_multiplier(-2), 1.5)
        self.assertEqual(goal_multiplier(3), 1.75)

    def test_zero_sum_and_direction(self):
        engine = EloEngine(EloParams())
        u = engine.process("X", "2425", "A", "B", 2, 0)
        self.assertAlmostEqual(u.home_pre + u.away_pre, u.home_post + u.away_post)
        self.assertGreater(u.home_post, u.home_pre)
        self.assertTrue(u.warmup)


class SeasonTests(unittest.TestCase):
    def test_regression_and_promoted_team(self):
        p = EloParams(k=40, season_carryover=0.5, promoted_pool=1)
        engine = EloEngine(p)
        for _ in range(10):
            engine.process("X", "2324", "Strong", "Weak", 3, 0)
        spread_before = engine.ratings["Strong"] - engine.ratings["Weak"]
        weak_before = engine.ratings["Weak"]

        u = engine.process("X", "2425", "Strong", "Promoted", 1, 1)
        self.assertAlmostEqual(u.home_pre - 1500, spread_before / 2 * 0.5)
        # Aufsteiger startet beim schwächsten Vorjahresteam (nach Regression)
        self.assertAlmostEqual(u.away_pre, 1500 + (weak_before - 1500) * 0.5)
        self.assertFalse(u.warmup)


class ComputeAllTests(unittest.TestCase):
    def test_pipeline_in_memory(self):
        conn = db.connect(":memory:")
        for date, h, a, hg, ag in [("2024-08-20", "A", "B", 1, 0), ("2024-08-27", "B", "A", 2, 2)]:
            db.upsert_match(conn, {"competition": "X", "season": "2425", "match_date": date,
                                   "home_team": h, "away_team": a,
                                   "home_goals": hg, "away_goals": ag}, "test", "now")
        self.assertEqual(compute_all(conn), 2)
        rows = conn.execute("SELECT * FROM elo_history ORDER BY match_id").fetchall()
        self.assertEqual(rows[0]["home_elo_pre"], 1500)
        self.assertEqual(rows[1]["away_elo_pre"], rows[0]["home_elo_post"])  # A war vorher Heimteam
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM team_ratings").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
