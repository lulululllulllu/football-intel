import unittest

from fi import db
from fi.providers.api_football import (ApiError, ApiFootball, QuotaExceeded,
                                       normalize_fixture, raise_on_errors)


class ApiFootballTests(unittest.TestCase):
    def test_quota_blocks_before_network(self):
        conn = db.connect(":memory:")
        api = ApiFootball("dummy", conn, daily_limit=100, reserve=5)
        conn.execute("INSERT INTO api_usage VALUES ('api-football', ?, 95)", (api._today(),))
        with self.assertRaises(QuotaExceeded):
            api._get("/fixtures")

    def test_errors(self):
        raise_on_errors({"errors": []})
        raise_on_errors({"errors": {}})
        with self.assertRaises(ApiError):
            raise_on_errors({"errors": {"plan": "season not available"}})

    def test_normalize_fixture(self):
        item = {"fixture": {"id": 7, "date": "2026-09-19T15:30:00+02:00", "status": {"short": "NS"}},
                "league": {"id": 78, "name": "Bundesliga", "season": 2026},
                "teams": {"home": {"name": "Team A"}, "away": {"name": "Team B"}},
                "goals": {"home": None, "away": None}}
        f = normalize_fixture(item)
        self.assertEqual((f["fixture_id"], f["league_id"], f["status"]), (7, 78, "NS"))
        self.assertIsNone(f["home_goals"])


if __name__ == "__main__":
    unittest.main()
