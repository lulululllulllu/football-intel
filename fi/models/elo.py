"""Elo-Modell für nationale Ligen.

Funktionsweise:
1. Jedes Team hat eine Zahl (Rating). Differenz der Ratings + Heimvorteil
   ergibt die erwartete Punktausbeute des Heimteams (0 bis 1).
2. Nach dem Spiel wird verglichen: tatsächlich (Sieg 1, Remis 0,5, Niederlage 0)
   minus erwartet. Die Differenz, multipliziert mit K und einem Faktor für die
   Tordifferenz, wandert vom einen zum anderen Team (Nullsumme).
3. Zwischen zwei Saisons rücken alle Teams einer Liga ein Stück zum Liga-Mittel,
   weil sich Kader im Sommer verändern.
4. Aufsteiger starten mit dem Mittel der schwächsten Teams der Vorsaison.

Wichtig: Gespeichert werden die Werte VOR jedem Spiel. Nur diese dürfen
später für Prognosen und Backtests genutzt werden (kein Lookahead).

Einschränkung: Ohne Europapokalspiele sind Ratings verschiedener Ligen nicht
vergleichbar. Dafür wird ClubElo genutzt.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from fi import config, db


@dataclass(frozen=True)
class EloParams:
    initial: float = 1500.0
    k: float = 20.0
    home_advantage: float = 65.0
    season_carryover: float = 0.80
    promoted_pool: int = 3

    @classmethod
    def from_config(cls) -> "EloParams":
        return cls(**config.ELO)


@dataclass(frozen=True)
class EloUpdate:
    home_pre: float
    away_pre: float
    expected_home: float
    home_post: float
    away_post: float
    warmup: bool


def expected_home_score(home_elo: float, away_elo: float, home_advantage: float) -> float:
    return 1.0 / (1.0 + 10 ** (-(home_elo + home_advantage - away_elo) / 400.0))


def goal_multiplier(goal_diff: int) -> float:
    """Klare Siege verändern das Rating stärker als knappe."""
    diff = abs(goal_diff)
    if diff <= 1:
        return 1.0
    if diff == 2:
        return 1.5
    return (11 + diff) / 8


def actual_score(home_goals: int, away_goals: int) -> float:
    if home_goals > away_goals:
        return 1.0
    return 0.5 if home_goals == away_goals else 0.0


class EloEngine:
    def __init__(self, params: EloParams):
        self.p = params
        self.ratings: dict[str, float] = {}
        self._league_season: dict[str, str] = {}
        self._season_teams: dict[str, set[str]] = {}
        self._prev_season_teams: dict[str, set[str]] = {}
        self._first_season: dict[str, str] = {}

    def _start_new_season(self, league: str, season: str) -> None:
        previous = self._season_teams.get(league, set())
        if previous:
            mean = sum(self.ratings[t] for t in previous) / len(previous)
            for team in previous:
                self.ratings[team] = mean + self.p.season_carryover * (self.ratings[team] - mean)
        self._prev_season_teams[league] = previous
        self._season_teams[league] = set()
        self._league_season[league] = season

    def _promoted_rating(self, league: str) -> float:
        previous = self._prev_season_teams[league]
        lowest = sorted(self.ratings[t] for t in previous)[: self.p.promoted_pool]
        return sum(lowest) / len(lowest)

    def _register(self, team: str, league: str) -> None:
        if team in self._season_teams[league]:
            return
        previous = self._prev_season_teams.get(league, set())
        if previous and team not in previous:
            self.ratings[team] = self._promoted_rating(league)   # Aufsteiger
        elif team not in self.ratings:
            self.ratings[team] = self.p.initial                  # allererste Saison
        self._season_teams[league].add(team)

    def process(self, league: str, season: str, home: str, away: str,
                home_goals: int, away_goals: int) -> EloUpdate:
        self._first_season.setdefault(league, season)
        if self._league_season.get(league) != season:
            self._start_new_season(league, season)
        self._register(home, league)
        self._register(away, league)

        home_pre, away_pre = self.ratings[home], self.ratings[away]
        expected = expected_home_score(home_pre, away_pre, self.p.home_advantage)
        delta = (self.p.k * goal_multiplier(home_goals - away_goals)
                 * (actual_score(home_goals, away_goals) - expected))
        self.ratings[home] = home_pre + delta
        self.ratings[away] = away_pre - delta
        return EloUpdate(home_pre, away_pre, expected, self.ratings[home], self.ratings[away],
                         warmup=season == self._first_season[league])


def compute_all(conn: sqlite3.Connection, params: EloParams | None = None) -> int:
    """Berechnet alle Elo-Werte chronologisch neu (deterministisch, jederzeit wiederholbar)."""
    engine = EloEngine(params or EloParams.from_config())
    rows = conn.execute(
        "SELECT id, competition, season, match_date, home_team, away_team, home_goals, away_goals "
        "FROM matches WHERE home_goals IS NOT NULL AND away_goals IS NOT NULL "
        "ORDER BY match_date, COALESCE(kick_time, ''), id"
    ).fetchall()
    now = db.utc_now()
    history, last_seen = [], {}
    for r in rows:
        u = engine.process(r["competition"], r["season"], r["home_team"], r["away_team"],
                           r["home_goals"], r["away_goals"])
        history.append((r["id"], u.home_pre, u.away_pre, u.expected_home, u.home_post,
                        u.away_post, int(u.warmup), config.ELO_MODEL_VERSION, now))
        for team in (r["home_team"], r["away_team"]):
            last_seen[team] = (r["competition"], r["season"], r["match_date"])

    with conn:
        conn.execute("DELETE FROM elo_history")
        conn.executemany("INSERT INTO elo_history VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", history)
        conn.execute("DELETE FROM team_ratings")
        conn.executemany(
            "INSERT INTO team_ratings VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(team, comp, season, engine.ratings[team], date, config.ELO_MODEL_VERSION, now)
             for team, (comp, season, date) in last_seen.items()],
        )
    return len(rows)
