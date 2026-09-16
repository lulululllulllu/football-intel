"""Backtest-Tests mit SYNTHETISCHEN Spielen."""
import datetime as dt
import unittest

from fi import backtest, db
from fi.models.elo import EloParams
from fi.models.goals import GoalParams
from tests.test_goals import synthetic_league


def _db_with_league(rounds=10, start=dt.date(2019, 8, 1)):
    conn = db.connect(":memory:")
    matches, _ = synthetic_league(rounds=rounds, start=start)
    ids = []
    for m in matches:
        ids.append(db.upsert_match(conn, {"competition": "D1", "season": "1920",
                                          "match_date": m.match_date.isoformat(),
                                          "home_team": m.home, "away_team": m.away,
                                          "home_goals": m.home_goals, "away_goals": m.away_goals}, "s", "t"))
        db.upsert_odds(conn, ids[-1], [("pinnacle", "pre", "1x2", s, p) for s, p in
                                       (("H", 2.1), ("D", 3.4), ("A", 3.5))]
                       + [("pinnacle", "closing", "1x2", s, p) for s, p in
                          (("H", 2.0), ("D", 3.5), ("A", 3.6))], "t")
    return conn, matches


class MetricTests(unittest.TestCase):
    def test_metrics(self):
        self.assertAlmostEqual(backtest.log_loss({"H": 1.0, "D": 0.0, "A": 0.0}, "H"), 0.0)
        self.assertAlmostEqual(backtest.brier({"H": 0.5, "D": 0.25, "A": 0.25}, "H"), 0.375)
        self.assertEqual(backtest.outcome_of(1, 1), {"1x2": "D", "ou25": "under", "btts": "yes"})


class RunTests(unittest.TestCase):
    params = GoalParams(window_days=2000)

    def test_run_and_compare(self):
        conn, _ = _db_with_league()
        records, _ = backtest.run_league(conn, "D1", 2019, self.params, EloParams())
        self.assertGreater(len(records), 100)
        cmp = backtest.compare(records, "1x2")
        self.assertEqual(cmp["n"], len(records))
        self.assertAlmostEqual(cmp["blends"][1.0], cmp["model"][0])
        self.assertIsNone(backtest.compare(records, "ou25"))  # keine Ü/U-Quoten in den Testdaten

    def test_no_lookahead(self):
        conn, matches = _db_with_league()
        before, _ = backtest.run_league(conn, "D1", 2019, self.params, EloParams())
        last_date = matches[-1].match_date.isoformat()
        with conn:  # Ergebnisse des letzten Spieltags drastisch verändern
            conn.execute("UPDATE matches SET home_goals = 9, away_goals = 0 WHERE match_date = ?",
                         (last_date,))
        after, _ = backtest.run_league(conn, "D1", 2019, self.params, EloParams())
        target = [i for i, r in enumerate(before) if r.match_date == last_date]
        self.assertTrue(target)
        for i in target:  # Prognose unverändert, nur das Ergebnis ist anders
            self.assertEqual(before[i].model, after[i].model)


if __name__ == "__main__":
    unittest.main()
