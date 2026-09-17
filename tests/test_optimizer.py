"""Optimierer-Tests mit TESTDATEN."""
import datetime as dt
import unittest

from fi import db, optimizer
from fi.optimizer import Leg


def leg(match_id, price, p, selection="H", p_model=None):
    return Leg(match_id, "D1", "2026-09-19 15:30", f"Heim{match_id}", f"Gast{match_id}", "1x2", selection,
               price, "skybet", p, p_model, 8)


class OptimizeTests(unittest.TestCase):
    def test_prefers_fewer_legs_at_same_odds(self):
        legs = [leg(1, 3.0, 0.32),                   # Einzelwette, Rückzahlung 96 %
                leg(2, 1.75, 0.55), leg(3, 1.72, 0.56)]  # Kombi 3.01, Rückzahlung ~93 %
        best = optimizer.optimize(legs, target=3.0)[0]
        self.assertEqual([l.match_id for l in best.legs], [1])

    def test_one_leg_per_match_and_band(self):
        legs = [leg(1, 1.8, 0.53, "H"), leg(1, 1.9, 0.50, "over"), leg(2, 1.7, 0.57)]
        for s in optimizer.optimize(legs, target=3.2, tolerance=0.1):
            self.assertEqual(len({l.match_id for l in s.legs}), len(s.legs))
            self.assertTrue(2.88 <= s.total_odds <= 3.52)

    def test_nothing_in_band(self):
        self.assertEqual(optimizer.optimize([leg(1, 1.2, 0.8)], target=10.0, max_legs=2), [])


class CollectTests(unittest.TestCase):
    def test_only_my_bookmakers_fresh_odds_and_model_gap(self):
        conn = db.connect(":memory:")
        now = dt.datetime(2026, 9, 18, 10, 0, tzinfo=dt.timezone.utc)
        mid = db.upsert_match(conn, {"competition": "UEL", "season": "2627", "match_date": "2026-09-19",
                                     "kick_time": "20:00", "home_team": "A", "away_team": "B"}, "s", "t")
        fetched = (now - dt.timedelta(hours=2)).isoformat(timespec="seconds")
        odds = []
        for book, prices in (("skybet", (2.0, 3.4, 3.8)), ("unibet_uk", (2.1, 3.3, 3.6)),
                             ("betway", (2.05, 3.5, 3.5)), ("mybookieag", (2.5, 3.6, 4.0))):
            odds += [(book, "current", "1x2", s, p) for s, p in zip("HDA", prices)]
        db.upsert_odds(conn, mid, odds, fetched)
        legs, _ = optimizer.collect_legs(conn, now, days=2, allowed=["skybet"], use_model=False)
        self.assertEqual({l.bookmaker for l in legs}, {"skybet"})  # MyBookie hat bessere Quoten, zählt aber nicht
        self.assertEqual(len(legs), 2)  # Auswärtssieg zu 3.80 liegt über der Außenseiter-Grenze 3.5
        stale, _ = optimizer.collect_legs(conn, now + dt.timedelta(hours=31), days=2, allowed=["skybet"],
                                          use_model=False)
        self.assertEqual(stale, [])  # veraltete Quoten werden ignoriert


if __name__ == "__main__":
    unittest.main()


class MatchExclusionTests(unittest.TestCase):
    def test_whole_match_excluded_and_berlin_time(self):
        from unittest import mock
        conn = db.connect(":memory:")
        now = dt.datetime(2026, 9, 18, 10, 0, tzinfo=dt.timezone.utc)
        mid = db.upsert_match(conn, {"competition": "D1", "season": "2627", "match_date": "2026-09-19",
                                     "kick_time": "14:30", "home_team": "A", "away_team": "B"}, "s", "t")
        fetched = (now - dt.timedelta(hours=1)).isoformat(timespec="seconds")
        odds = []
        for book in ("skybet", "unibet_uk", "betway"):
            odds += [(book, "current", "1x2", s, p) for s, p in zip("HDA", (2.0, 3.4, 3.8))]
        db.upsert_odds(conn, mid, odds, fetched)
        model = {"1x2": {"H": 0.70, "D": 0.20, "A": 0.10}}  # weit weg vom Markt beim Heimsieg
        fake_items = [{"match": {"id": mid}, "model": model}]
        with mock.patch("fi.optimizer.predict_upcoming", return_value=fake_items):
            legs, notes = optimizer.collect_legs(conn, now, days=2, allowed=["skybet"])
        self.assertEqual(legs, [])
        self.assertTrue(notes[0].startswith("Spiel ausgeschlossen"))
        with mock.patch("fi.optimizer.predict_upcoming", return_value=[]):
            legs, _ = optimizer.collect_legs(conn, now, days=2, allowed=["skybet"])
        self.assertEqual(legs[0].kickoff_local, "Sa 19.09. 15:30")


class SiteTests(unittest.TestCase):
    def test_render_escapes_script_end(self):
        from fi import site
        html = site.render({"generated_local": "16.09.2026, 11:00", "x": "</script><b>"})
        self.assertNotIn("</script><b>", html)
        self.assertIn("Stand 16.09.2026, 11:00", html)
