"""Tests der Korrekturen aus der historischen Untersuchung. TESTDATEN."""
import unittest

from fi import adjust, db, optimizer
from fi.models.goals import markets, score_matrix
from tests.test_optimizer import leg


class CalibrationTests(unittest.TestCase):
    def test_favourite_up_longshot_down_middle_unchanged(self):
        out = adjust.calibrate({"H": 0.78, "D": 0.14, "A": 0.08})
        self.assertGreater(out["H"], 0.78)
        self.assertLess(out["A"], 0.08)
        self.assertAlmostEqual(sum(out.values()), 1.0)
        middle = adjust.calibrate({"H": 0.45, "D": 0.27, "A": 0.28})
        self.assertAlmostEqual(middle["H"], 0.45)

    def test_loss_streak_and_exclusion(self):
        conn = db.connect(":memory:")
        for day in range(1, 7):
            db.upsert_match(conn, {"competition": "D1", "season": "2627", "match_date": f"2026-09-0{day}",
                                   "home_team": "Pech", "away_team": f"G{day}", "home_goals": 0,
                                   "away_goals": 1}, "s", "t")
        self.assertEqual(adjust.loss_streak(conn, "Pech", "2026-09-10"), 6)
        match = {"home_team": "Pech", "away_team": "Gast", "match_date": "2026-09-10",
                 "competition": "D1", "season": "2627"}
        _, excluded, notes = adjust.adjust(conn, match, "1x2", {"H": 0.5, "D": 0.25, "A": 0.25})
        self.assertEqual(excluded, {"H"})
        self.assertIn("Niederlagen in Folge", notes[0])

    def test_goal_expectations_reproduce_market(self):
        target = markets(score_matrix(1.9, 0.9, -0.05, 8))
        lh, la = adjust.fit_goal_expectations(target["1x2"], target["ou25"])
        self.assertAlmostEqual(lh, 1.9, delta=0.08)
        self.assertAlmostEqual(la, 0.9, delta=0.08)


class DayBetTests(unittest.TestCase):
    def test_highest_chance_in_range_with_min_return(self):
        legs = [leg(1, 1.55, 0.62),   # Rückzahlung 96 %, Chance 62 %
                leg(2, 1.50, 0.66),   # Rückzahlung 99 %, Chance 66 %  <- beste
                leg(3, 1.60, 0.56),   # Rückzahlung 90 %: zu schlecht
                leg(4, 2.40, 0.40)]   # außerhalb des Bereichs
        best = optimizer.best_in_range(legs, 1.5, 2.0, max_legs=1, min_return=0.95)
        self.assertEqual(best.legs[0].match_id, 2)
        self.assertIsNone(optimizer.best_in_range([leg(3, 1.60, 0.56)], 1.5, 2.0, min_return=0.95))


if __name__ == "__main__":
    unittest.main()
