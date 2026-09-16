"""Tests mit kleinen, von Hand geschriebenen TESTDATEN (keine echten Spiele)."""
import unittest

from fi.providers import football_data_uk as fduk

SAMPLE = (
    "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HS,AS,HST,AST,"
    "B365H,B365D,B365A,PSCH,PSCD,PSCA,P>2.5,P<2.5,\r\n"
    "D1,23/08/2024,19:30,Team A,Team B,2,3,A,10,15,4,6,5.50,4.50,1.57,5.80,4.60,1.60,1.55,2.50,\r\n"
    "D1,24/08/24,,Team C,Team D,,,,,,,,1.20,7.00,12.00,,,,,,\r\n"
    ",,,,,,,,,,,,,,,,,,,,\r\n"
).encode()


class ParseTests(unittest.TestCase):
    def test_only_played_matches(self):
        matches = fduk.parse_results_csv(SAMPLE, "D1", 2024)
        self.assertEqual(len(matches), 1)
        m = matches[0]
        self.assertEqual(m["match_date"], "2024-08-23")
        self.assertEqual(m["kick_time"], "19:30")
        self.assertEqual(m["season"], "2425")
        self.assertEqual((m["home_goals"], m["away_goals"]), (2, 3))
        self.assertEqual((m["home_shots_target"], m["away_shots_target"]), (4, 6))

    def test_odds_mapping(self):
        odds = fduk.parse_results_csv(SAMPLE, "D1", 2024)[0]["odds"]
        self.assertEqual(len(odds), 8)
        self.assertIn(("pinnacle", "closing", "1x2", "H", 5.80), odds)
        self.assertIn(("pinnacle", "pre", "ou25", "under", 2.50), odds)

    def test_two_digit_year_and_latin1(self):
        raw = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG\nD1,01/09/19,K\xf6ln,Team B,1,1\n".encode("latin-1")
        m = fduk.parse_results_csv(raw, "D1", 2019)[0]
        self.assertEqual(m["home_team"], "Köln")
        self.assertEqual(m["match_date"], "2019-09-01")
        self.assertEqual(m["odds"], [])


class FixturesTests(unittest.TestCase):
    def test_fixtures_filter_and_empty_goals(self):
        raw = ("Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,PSH,PSD,PSA\n"
               "D1,19/09/2026,15:30,Team A,Team B,,,1.80,3.90,4.20\n"
               "SC0,19/09/2026,15:00,Team X,Team Y,,,2.0,3.3,3.6\n").encode()
        fixtures = fduk.parse_fixtures_csv(raw, ["D1"])
        self.assertEqual(len(fixtures), 1)
        self.assertEqual(fixtures[0]["season"], "2627")
        self.assertIsNone(fixtures[0]["home_goals"])
        self.assertEqual(len(fixtures[0]["odds"]), 3)


if __name__ == "__main__":
    unittest.main()
