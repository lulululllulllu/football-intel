"""Tormodell nach Dixon & Coles (1997).

Idee:
- Jedes Team hat eine Angriffsstärke (attack) und eine Abwehrschwäche (defence),
  beide um 1,0 = Liga-Durchschnitt.
- Erwartete Tore Heim = mu * Heimvorteil * attack[Heim] * defence[Gast]
  Erwartete Tore Gast = mu * attack[Gast] * defence[Heim]
- Tore folgen ungefähr einer Poisson-Verteilung. Daraus entsteht eine Matrix mit der
  Wahrscheinlichkeit jedes Ergebnisses (0:0, 1:0, ...). Die Dixon-Coles-Korrektur rho
  passt die niedrigen Ergebnisse an, die reines Poisson schlecht trifft.
- Alle Märkte (1X2, Über/Unter, beide treffen) werden aus DERSELBEN Matrix abgeleitet.
  Deshalb lassen sich später auch Kombinationen innerhalb eines Spiels korrekt berechnen.

Schätzung:
- Neuere Spiele zählen stärker (Halbwertszeit).
- Wenige Spiele werden mit "gedachten Spielen" auf Elo-Niveau stabilisiert (Prior).
  So springen Aufsteiger oder Teams nach drei Spieltagen nicht auf extreme Werte.
- Es fließen ausschließlich Spiele VOR dem Stichtag ein (kein Lookahead).
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

from fi import config


@dataclass(frozen=True)
class GoalParams:
    window_days: int = 900
    half_life_days: float = 300.0
    prior_matches: float = 3.0
    elo_prior_beta: float = 0.6
    max_goals: int = 10
    iterations: int = 300

    @classmethod
    def from_config(cls) -> "GoalParams":
        return cls(**config.GOALS)


@dataclass(frozen=True)
class MatchResult:
    match_date: dt.date
    home: str
    away: str
    home_goals: int
    away_goals: int


@dataclass
class GoalModel:
    mu: float
    home_advantage: float
    attack: dict[str, float]
    defence: dict[str, float]
    rho: float
    cutoff: dt.date
    n_matches: int
    effective_games: dict[str, float] = field(default_factory=dict)

    def expected_goals(self, home: str, away: str) -> tuple[float, float] | None:
        """None, wenn für ein Team keine Daten vorliegen. Es wird nichts geschätzt."""
        if home not in self.attack or away not in self.attack:
            return None
        return (self.mu * self.home_advantage * self.attack[home] * self.defence[away],
                self.mu * self.attack[away] * self.defence[home])


def time_weight(days_ago: int, half_life_days: float) -> float:
    return 0.5 ** (days_ago / half_life_days)


def tau(home_goals: int, away_goals: int, lam_home: float, lam_away: float, rho: float) -> float:
    """Dixon-Coles-Korrektur für die Ergebnisse 0:0, 1:0, 0:1 und 1:1."""
    if home_goals == 0 and away_goals == 0:
        return 1 - lam_home * lam_away * rho
    if home_goals == 0 and away_goals == 1:
        return 1 + lam_home * rho
    if home_goals == 1 and away_goals == 0:
        return 1 + lam_away * rho
    if home_goals == 1 and away_goals == 1:
        return 1 - rho
    return 1.0


def _poisson_pmf(lam: float, max_goals: int) -> list[float]:
    probs = [math.exp(-lam)]
    for k in range(1, max_goals + 1):
        probs.append(probs[-1] * lam / k)
    return probs


def score_matrix(lam_home: float, lam_away: float, rho: float, max_goals: int = 10) -> list[list[float]]:
    """matrix[h][a] = Wahrscheinlichkeit für das Ergebnis h:a (Summe = 1)."""
    ph, pa = _poisson_pmf(lam_home, max_goals), _poisson_pmf(lam_away, max_goals)
    matrix = [[ph[h] * pa[a] * max(tau(h, a, lam_home, lam_away, rho), 0.0)
               for a in range(max_goals + 1)] for h in range(max_goals + 1)]
    total = sum(map(sum, matrix))
    return [[p / total for p in row] for row in matrix]


def probability(matrix: list[list[float]], condition) -> float:
    """Wahrscheinlichkeit eines beliebigen Ereignisses, z.B. lambda h, a: h > a and h + a > 2."""
    return sum(p for h, row in enumerate(matrix) for a, p in enumerate(row) if condition(h, a))


def markets(matrix: list[list[float]]) -> dict[str, dict[str, float]]:
    return {
        "1x2": {"H": probability(matrix, lambda h, a: h > a),
                "D": probability(matrix, lambda h, a: h == a),
                "A": probability(matrix, lambda h, a: h < a)},
        "ou25": {"over": probability(matrix, lambda h, a: h + a > 2),
                 "under": probability(matrix, lambda h, a: h + a <= 2)},
        "btts": {"yes": probability(matrix, lambda h, a: h > 0 and a > 0),
                 "no": probability(matrix, lambda h, a: h == 0 or a == 0)},
    }


def _elo_priors(teams, elo: dict[str, float] | None, beta: float):
    known = [elo[t] for t in teams if elo and t in elo]
    if not known:
        return {t: 1.0 for t in teams}, {t: 1.0 for t in teams}
    mean = sum(known) / len(known)
    attack = {t: math.exp(beta * (elo[t] - mean) / 400) if t in elo else 1.0 for t in teams}
    return attack, {t: 1.0 / attack[t] for t in teams}


def _normalize(values: dict[str, float]) -> dict[str, float]:
    mean = sum(values.values()) / len(values)
    return {t: v / mean for t, v in values.items()}


def fit(matches: list[MatchResult], cutoff: dt.date, params: GoalParams | None = None,
        elo: dict[str, float] | None = None, init: GoalModel | None = None) -> GoalModel | None:
    """Schätzt das Modell aus Spielen vor `cutoff`. None, wenn die Datenbasis zu klein ist.

    `init`: Ergebnis einer früheren Schätzung als Startpunkt. Ändert das Ergebnis nicht,
    spart aber viel Rechenzeit im Backtest (wenige statt hunderter Durchläufe).
    """
    p = params or GoalParams()
    rows = []
    for m in matches:
        days_ago = (cutoff - m.match_date).days
        if 0 < days_ago <= p.window_days:
            rows.append((m, time_weight(days_ago, p.half_life_days)))
    if len(rows) < 30:
        return None

    teams = sorted({m.home for m, _ in rows} | {m.away for m, _ in rows})
    games: dict[str, list] = {t: [] for t in teams}
    effective = {t: 0.0 for t in teams}
    for m, w in rows:
        games[m.home].append((w, m.home_goals, m.away_goals, m.away, True))
        games[m.away].append((w, m.away_goals, m.home_goals, m.home, False))
        effective[m.home] += w
        effective[m.away] += w

    prior_att, prior_def = _elo_priors(teams, elo, p.elo_prior_beta)
    c = p.prior_matches
    weight_sum = sum(w for _, w in rows)
    if init is not None:
        attack = _normalize({t: init.attack.get(t, prior_att[t]) for t in teams})
        defence = _normalize({t: init.defence.get(t, prior_def[t]) for t in teams})
        mu, home_adv = init.mu, init.home_advantage
    else:
        attack = {t: 1.0 for t in teams}
        defence = {t: 1.0 for t in teams}
        mu = sum(w * (m.home_goals + m.away_goals) for m, w in rows) / (2 * weight_sum)
        home_adv = 1.0

    for _ in range(p.iterations):
        old_attack, old_defence, old = attack, defence, (mu, home_adv)
        new_attack = {}
        for t in teams:
            scored = expected = 0.0
            for w, goals_for, _, opp, is_home in games[t]:
                scored += w * goals_for
                expected += w * mu * defence[opp] * (home_adv if is_home else 1.0)
            new_attack[t] = (scored + c * mu * prior_att[t]) / (expected + c * mu)
        attack = _normalize(new_attack)

        new_defence = {}
        for t in teams:
            conceded = expected = 0.0
            for w, _, goals_against, opp, is_home in games[t]:
                conceded += w * goals_against
                expected += w * mu * attack[opp] * (1.0 if is_home else home_adv)
            new_defence[t] = (conceded + c * mu * prior_def[t]) / (expected + c * mu)
        defence = _normalize(new_defence)

        home_adv = (sum(w * m.home_goals for m, w in rows)
                    / sum(w * mu * attack[m.home] * defence[m.away] for m, w in rows))
        mu = (sum(w * (m.home_goals + m.away_goals) for m, w in rows)
              / sum(w * (home_adv * attack[m.home] * defence[m.away] + attack[m.away] * defence[m.home])
                    for m, w in rows))
        change = max(max(abs(attack[t] - old_attack[t]) for t in teams),
                     max(abs(defence[t] - old_defence[t]) for t in teams),
                     abs(mu - old[0]), abs(home_adv - old[1]))
        if change < 1e-6:
            break

    model = GoalModel(mu, home_adv, attack, defence, 0.0, cutoff, len(rows), effective)
    model.rho = _fit_rho(rows, model)
    return model


def _fit_rho(rows, model: GoalModel) -> float:
    """Wählt rho mit der höchsten gewichteten Likelihood auf einem Raster von -0,25 bis 0,25."""
    low_scores = [(m, w, *model.expected_goals(m.home, m.away)) for m, w in rows
                  if m.home_goals <= 1 and m.away_goals <= 1]
    best_rho, best_ll = 0.0, -math.inf
    for step in range(-25, 26):
        rho = step / 100
        values = [(w, tau(m.home_goals, m.away_goals, lh, la, rho)) for m, w, lh, la in low_scores]
        if any(t <= 0 for _, t in values):
            continue
        ll = sum(w * math.log(t) for w, t in values)
        if ll > best_ll:
            best_rho, best_ll = rho, ll
    return best_rho
