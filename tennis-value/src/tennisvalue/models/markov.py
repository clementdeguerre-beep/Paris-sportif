"""Modèle de Markov hiérarchique : point -> jeu -> tie-break -> set -> match.

Hypothèse centrale (Klaassen & Magnus 2001 ; Barnett & Clarke 2005) : chaque
point au service est gagné avec une probabilité constante, indépendante des
points précédents. pa = probabilité que A gagne un point sur SON service,
pb = probabilité que B gagne un point sur SON service.

Le module donne :
- la probabilité de gagner le match (marché vainqueur) ;
- la distribution complète des scores en jeux et en sets (base des futurs
  marchés handicaps / totaux de jeux et de sets).

Règles du set décisif (Grand Chelem) gérées via `final_set_rule` :
- "tb7"  : tie-break à 7 points à 6-6 (règle standard) ;
- "tb10" : super tie-break à 10 points à 6-6 (tous les Grands Chelems depuis 2022) ;
- "adv"  : set à l'avantage, sans tie-break (Open d'Australie et Wimbledon
           jusqu'en 2018, Roland-Garros jusqu'en 2021). Wimbledon 2019-2021
           (tie-break à 12-12) est approximé par "adv" : écart négligeable.
"""
from __future__ import annotations

from collections import defaultdict
from functools import lru_cache

_ROUND = 4  # précision du cache (les probabilités de point sont arrondies à 1e-4)


def hold_prob(p: float) -> float:
    """Probabilité de remporter son jeu de service si l'on gagne chaque point avec p."""
    q = 1.0 - p
    # Victoire à 0, 15 ou 30 + atteinte de l'égalité (20 p^3 q^3) puis victoire depuis l'égalité.
    return p**4 * (1 + 4 * q + 10 * q * q) + 20 * p**3 * q**3 * (p * p) / (1 - 2 * p * q)


@lru_cache(maxsize=200_000)
def tiebreak_prob(pa: float, pb: float, target: int = 7) -> float:
    """Probabilité que A gagne le tie-break, A servant le premier point.

    Ordre de service : A sert le point 1, puis chaque joueur sert deux points.
    Au-delà de (target-1, target-1), chaque paire de points comporte un point
    servi par chacun : on utilise la formule fermée de l'égalité.
    """

    @lru_cache(maxsize=None)
    def f(a: int, b: int) -> float:
        if a >= target and a - b >= 2:
            return 1.0
        if b >= target and b - a >= 2:
            return 0.0
        if a == b and a >= target - 1:
            ww = pa * (1 - pb)
            ll = (1 - pa) * pb
            return ww / (ww + ll)
        k = a + b
        a_serving = ((k + 1) // 2) % 2 == 0
        p = pa if a_serving else 1 - pb
        return p * f(a + 1, b) + (1 - p) * f(a, b + 1)

    return f(0, 0)


@lru_cache(maxsize=200_000)
def set_distribution(pa: float, pb: float, a_serves_first: bool, rule: str = "tb7") -> tuple:
    """Distribution des scores finaux d'un set : tuple de ((jeux A, jeux B), proba).

    `rule` : "tb7", "tb10" ou "adv" (voir docstring du module).
    """
    ha, hb = hold_prob(pa), hold_prob(pb)
    states = {(0, 0): 1.0}
    final: dict = defaultdict(float)
    n = 0
    while states:
        nxt: dict = defaultdict(float)
        for (ga, gb), pr in states.items():
            if ga == 6 and gb == 6:
                _resolve_six_all(pa, pb, ha, hb, a_serves_first, rule, pr, final)
                continue
            a_serving = (n % 2 == 0) == a_serves_first
            pw = ha if a_serving else 1 - hb
            for na, nb, pp in ((ga + 1, gb, pw), (ga, gb + 1, 1 - pw)):
                if max(na, nb) >= 6 and abs(na - nb) >= 2:
                    final[(na, nb)] += pr * pp
                else:
                    nxt[(na, nb)] += pr * pp
        states = nxt
        n += 1
    return tuple(sorted(final.items()))


def _resolve_six_all(pa, pb, ha, hb, a_serves_first, rule, pr, final):
    """Fin de set à 6-6 : tie-break (7 ou 10 points) ou set à l'avantage."""
    if rule in ("tb7", "tb10"):
        target = 7 if rule == "tb7" else 10
        # À 6-6, 12 jeux ont été joués : le serveur du 1er jeu du set sert en premier au tie-break.
        if a_serves_first:
            p_tb = tiebreak_prob(round(pa, _ROUND), round(pb, _ROUND), target)
        else:
            p_tb = 1 - tiebreak_prob(round(pb, _ROUND), round(pa, _ROUND), target)
        final[(7, 6)] += pr * p_tb
        final[(6, 7)] += pr * (1 - p_tb)
    else:  # set à l'avantage : paires de jeux (un service chacun)
        ww = ha * (1 - hb)
        ll = (1 - ha) * hb
        split = 1 - ww - ll
        mass = pr
        for k in range(60):  # 6-6 -> 8-6, 9-7, ... (troncature au-delà de 60 paires, masse ~0)
            final[(8 + k, 6 + k)] += mass * ww
            final[(6 + k, 8 + k)] += mass * ll
            mass *= split
        # masse résiduelle (négligeable) répartie au prorata
        tot = ww + ll
        final[(8 + 60, 6 + 60)] += mass * ww / tot
        final[(6 + 60, 8 + 60)] += mass * ll / tot


def _next_first_server(a_served_first: bool, ga: int, gb: int, rule: str) -> bool:
    """Qui sert en premier au set suivant : alterne si le nombre de jeux (tie-break = 1 jeu) est impair."""
    games = ga + gb
    return a_served_first if games % 2 == 0 else not a_served_first


@lru_cache(maxsize=200_000)
def _match_prob_given_first(pa: float, pb: float, best_of: int, final_rule: str, a_first: bool) -> float:
    sets_to_win = best_of // 2 + 1
    states = {(0, 0, a_first): 1.0}
    win = 0.0
    while states:
        nxt: dict = defaultdict(float)
        for (sa, sb, af), pr in states.items():
            is_final = sa == sets_to_win - 1 and sb == sets_to_win - 1
            rule = final_rule if is_final else "tb7"
            for (ga, gb), ps in set_distribution(pa, pb, af, rule):
                nsa, nsb = (sa + 1, sb) if ga > gb else (sa, sb + 1)
                if nsa == sets_to_win:
                    win += pr * ps
                elif nsb == sets_to_win:
                    pass
                else:
                    nxt[(nsa, nsb, _next_first_server(af, ga, gb, rule))] += pr * ps
        states = nxt
    return win


def match_prob(pa: float, pb: float, best_of: int = 3, final_rule: str = "tb7") -> float:
    """P(A gagne le match). Moyenne sur le tirage au sort du premier serveur (50/50)."""
    pa, pb = round(pa, _ROUND), round(pb, _ROUND)
    return 0.5 * (
        _match_prob_given_first(pa, pb, best_of, final_rule, True)
        + _match_prob_given_first(pa, pb, best_of, final_rule, False)
    )


def match_distribution(pa: float, pb: float, best_of: int = 3, final_rule: str = "tb7") -> dict:
    """Distribution jointe (sets A, sets B, jeux A, jeux B) -> proba.

    Sert aux marchés handicaps et totaux (jeux et sets). Moyenne sur le premier serveur.
    """
    pa, pb = round(pa, _ROUND), round(pb, _ROUND)
    sets_to_win = best_of // 2 + 1
    out: dict = defaultdict(float)
    for a_first in (True, False):
        states = {(0, 0, 0, 0, a_first): 0.5}
        while states:
            nxt: dict = defaultdict(float)
            for (sa, sb, ga_tot, gb_tot, af), pr in states.items():
                is_final = sa == sets_to_win - 1 and sb == sets_to_win - 1
                rule = final_rule if is_final else "tb7"
                for (ga, gb), ps in set_distribution(pa, pb, af, rule):
                    nsa, nsb = (sa + 1, sb) if ga > gb else (sa, sb + 1)
                    key = (nsa, nsb, ga_tot + ga, gb_tot + gb)
                    if nsa == sets_to_win or nsb == sets_to_win:
                        out[key] += pr * ps
                    else:
                        nxt[key + (_next_first_server(af, ga, gb, rule),)] += pr * ps
            states = nxt
    return dict(out)


def expected_game_share(pa: float, pb: float) -> float:
    """Part de jeux attendue pour A (approximation : chacun sert la moitié des jeux)."""
    return 0.5 * hold_prob(pa) + 0.5 * (1 - hold_prob(pb))


def final_set_rule(tier: str, year: int, tournament: str) -> str:
    """Règle du set décisif selon le tournoi et l'année (voir docstring du module)."""
    if tier != "Grand Slam":
        return "tb7"
    if year >= 2022:
        return "tb10"
    t = (tournament or "").lower()
    if "australian" in t:
        return "tb10" if year >= 2019 else "adv"
    if "french" in t or "roland" in t:
        return "adv"
    if "wimbledon" in t:
        return "adv"  # 2019-2021 : tie-break à 12-12, approximé par l'avantage
    return "tb7"  # US Open : tie-break à 7 points jusqu'en 2021
