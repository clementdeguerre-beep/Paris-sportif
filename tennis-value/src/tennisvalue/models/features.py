"""Construction des variables d'avant-match, sans fuite de données.

Les matchs sont parcourus dans l'ordre chronologique. Pour chaque match, on
calcule d'abord toutes les variables avec l'état connu AVANT le match, puis on
met à jour l'état avec le résultat. Aucune variable d'un match ne peut donc
dépendre de son propre résultat ni d'un match postérieur (vérifié par
tests/test_no_leakage.py).

Variables produites (joueur 1 vs joueur 2, ordre tiré au hasard) :
- Elo global, Elo surface, Elo mélangé et probabilités associées ;
- note de domination aux points + probabilité Markov ;
- classement officiel (au début du tournoi, fourni par tennis-data) ;
- fatigue : jeux et matchs joués sur 7 et 14 jours, longueur du dernier match, matchs
  sur 4 jours ;
- rouille / retour de blessure : jours depuis le dernier match, matchs joués depuis une
  longue pause, abandon ou forfait récent ;
- forme : surperformance sur les 10 derniers matchs (victoires - victoires attendues par
  l'Elo), sur la surface (12 mois), dans le tournoi (éditions précédentes) ;
- mental : tie-breaks et sets décisifs, matchs gagnés après la perte du premier set ;
- dynamique du classement sur 3 mois ;
- expérience : nombre de matchs dans la base ;
- confrontations directes.
Détail des facteurs de profil : models/profile.py.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from tennisvalue.models.markov import final_set_rule
from tennisvalue.models.profile import PlayerProfile, tournament_key
from tennisvalue.ratings.elo import EloSystem
from tennisvalue.ratings.points import PointRating

BOOK_KEYS = ("b365", "ps", "bfe", "max", "avg")


def _flip(match_id: str) -> bool:
    """Tirage déterministe : le vainqueur est-il placé en joueur 2 ?"""
    return int(hashlib.md5(match_id.encode()).hexdigest()[0], 16) % 2 == 1


@dataclass
class PlayerHistory:
    recent: dict = field(default_factory=lambda: defaultdict(deque))  # joueur -> (date, jeux)
    last: dict = field(default_factory=dict)
    h2h: dict = field(default_factory=lambda: defaultdict(int))  # (gagnant, perdant) -> nb

    def fatigue(self, player: str, d: date) -> dict:
        q = self.recent[player]
        while q and (d - q[0][0]).days > 14:
            q.popleft()
        g7 = sum(g for dd, g in q if (d - dd).days <= 7)
        g14 = sum(g for _, g in q)
        m14 = len(q)
        days = (d - self.last[player]).days if player in self.last else 365
        return {"games_7d": g7, "games_14d": g14, "matches_14d": m14, "days_since": min(days, 365)}

    def record(self, player: str, d: date, games: float) -> None:
        self.recent[player].append((d, games))
        self.last[player] = d


@dataclass
class FeatureState:
    """État complet d'un circuit (réutilisé pour scorer les matchs à venir)."""
    elo: EloSystem = field(default_factory=EloSystem)
    pts: PointRating = field(default_factory=PointRating)
    hist: PlayerHistory = field(default_factory=PlayerHistory)
    prof: PlayerProfile = field(default_factory=PlayerProfile)
    update_on_retirement: bool = False

    def pre_match(self, a: str, b: str, tour: str, surface: str, best_of: int, rule: str, d: date,
                  tkey: str | None = None) -> dict:
        out = {}
        e = self.elo.predict(a, b, surface)
        suffix = {"_a": "_1", "_b": "_2"}
        out.update({(k[:-2] + suffix[k[-2:]]) if k[-2:] in suffix else k: v for k, v in e.items()})
        out.update(self.pts.predict(a, b, tour, surface, best_of, rule))
        for side, p in (("1", a), ("2", b)):
            for k, v in self.hist.fatigue(p, d).items():
                out[f"{k}_{side}"] = v
            for k, v in self.prof.features(p, d, surface, tkey).items():
                out[f"{k}_{side}"] = v
        out["h2h_1"] = self.hist.h2h[(a, b)]
        out["h2h_2"] = self.hist.h2h[(b, a)]
        return out

    def post_match(self, r, tkey: str | None = None) -> None:
        exp_w = self.elo.predict(r.winner_key, r.loser_key, r.surface)["p_elo_blend"]
        self.prof.record(r, exp_w, tkey or tournament_key(r.location, r.surface, r.tier))
        if r.walkover:
            return
        games = r.w_games + r.l_games
        self.hist.record(r.winner_key, r.date, games)
        self.hist.record(r.loser_key, r.date, games)
        if r.completed or self.update_on_retirement:
            self.elo.update(r.winner_key, r.loser_key, r.surface)
            self.hist.h2h[(r.winner_key, r.loser_key)] += 1
        if r.completed:
            self.pts.update(r.winner_key, r.loser_key, r.tour, r.surface, r.w_games, r.l_games)


def build_features(matches: pd.DataFrame, pts_params: dict | None = None,
                   elo_params: dict | None = None) -> tuple[pd.DataFrame, dict]:
    """Renvoie (table des variables, états finaux par circuit)."""
    rows = []
    states: dict[str, FeatureState] = {}
    for tour, g in matches.groupby("tour", sort=False):
        st = FeatureState(elo=EloSystem(**(elo_params or {})), pts=PointRating(**(pts_params or {})))
        for r in g.itertuples(index=False):
            flip = _flip(r.match_id)
            a, b = (r.loser_key, r.winner_key) if flip else (r.winner_key, r.loser_key)
            rule = final_set_rule(r.tier, r.date.year, r.tournament)
            tkey = tournament_key(r.location, r.surface, r.tier)
            row = {
                "match_id": r.match_id, "tour": r.tour, "date": r.date, "season": r.season,
                "tournament": r.tournament, "tier": r.tier, "surface": r.surface, "court": r.court,
                "round": r.round, "best_of": r.best_of, "final_rule": rule,
                "p1": r.loser if flip else r.winner, "p2": r.winner if flip else r.loser,
                "p1_key": a, "p2_key": b, "y": 0 if flip else 1,
                "completed": r.completed, "walkover": r.walkover, "comment": r.comment,
                "rank_1": r.l_rank if flip else r.w_rank, "rank_2": r.w_rank if flip else r.l_rank,
            }
            for bk in BOOK_KEYS:
                ow, ol = getattr(r, f"odds_{bk}_w"), getattr(r, f"odds_{bk}_l")
                row[f"odds_{bk}_1"], row[f"odds_{bk}_2"] = (ol, ow) if flip else (ow, ol)
            row.update(st.pre_match(a, b, r.tour, r.surface, r.best_of, rule, r.date, tkey))
            rows.append(row)
            st.post_match(r, tkey)
        states[tour] = st
    feats = pd.DataFrame(rows)
    return add_derived(feats), states


def _logit(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def add_derived(f: pd.DataFrame) -> pd.DataFrame:
    """Variables dérivées utilisées par le modèle d'ensemble."""
    f = f.copy()
    r1 = f["rank_1"].fillna(1500).clip(1, 1500)
    r2 = f["rank_2"].fillna(1500).clip(1, 1500)
    f["x_logit_elo"] = _logit(f["p_elo"])
    f["x_logit_elo_surf"] = _logit(f["p_elo_surf"])
    f["x_logit_elo_blend"] = _logit(f["p_elo_blend"])
    f["x_logit_pts"] = _logit(f["p_pts"])
    f["x_log_rank_ratio"] = np.log(r2) - np.log(r1)
    f["x_exp"] = np.log1p(f["n_1"]) - np.log1p(f["n_2"])
    f["x_games14"] = (f["games_14d_1"] - f["games_14d_2"]) / 10.0
    f["x_games7"] = (f["games_7d_1"] - f["games_7d_2"]) / 10.0
    f["x_rust"] = np.log1p(f["days_since_1"]) - np.log1p(f["days_since_2"])
    f["x_h2h"] = (f["h2h_1"] - f["h2h_2"]) / (f["h2h_1"] + f["h2h_2"] + 2.0)
    f["x_bo5_elo"] = f["x_logit_elo_blend"] * (f["best_of"] == 5)
    for k in ("form10", "surf_form", "tourn_form", "clutch_tb", "clutch_dec", "comeback", "m4", "rusty",
              "injury", "rank_mom"):
        f[f"x_{k}"] = f[f"{k}_1"] - f[f"{k}_2"]
    f["x_last_games"] = (f["last_games_1"] - f["last_games_2"]) / 10.0
    return f


FEATURES = [
    "x_logit_elo", "x_logit_elo_surf", "x_logit_pts", "x_log_rank_ratio",
    "x_exp", "x_games14", "x_games7", "x_rust", "x_h2h", "x_bo5_elo",
    "x_form10", "x_surf_form", "x_tourn_form", "x_clutch_tb", "x_clutch_dec", "x_comeback",
    "x_last_games", "x_m4", "x_rusty", "x_injury", "x_rank_mom",
]
FEATURE_VERSION = 2   # à changer quand les variables changent : un ancien modèle doit être recalculé
