"""Profil d'avant-match d'un joueur : forme, surface, tournoi, physique, blessures, mental, classement.

Tout vient des seuls matchs déjà joués (`record` est appelé après chaque match) : un
match ne peut pas influencer son propre profil. Les taux sont « rétrécis » vers la
moyenne quand il y a peu de matchs (victoires et défaites fictives ajoutées), pour
qu'un 2 sur 2 ne vaille pas 100 %.

Surperformance = victoires réelles - victoires attendues d'après l'Elo au moment du
match. Un joueur qui gagne plus que son niveau ne le laissait prévoir est en forme, ou
à l'aise sur la surface, ou dans ce tournoi : c'est l'information ajoutée à l'Elo.

Mental, mesuré sur les scores : tie-breaks et sets décisifs gagnés comparés aux sets
gagnés en général (un joueur qui gagne 55 % de ses sets mais 70 % de ses tie-breaks
tient mieux la pression), et matchs gagnés après la perte du premier set.

Limites : tennis-data ne donne ni les stats de service et de retour, ni la durée des
matchs, ni la nationalité ; la fatigue se mesure donc en jeux joués et le style de jeu
ne se compare pas directement.
"""
from __future__ import annotations

import math
from collections import defaultdict, deque
from datetime import date
from typing import NamedTuple

from tennisvalue.ingest.tennisdata import norm_name

FORM_N = 10          # forme : 10 derniers matchs
SURF_DAYS = 365      # surface : 12 derniers mois
MENTAL_DAYS = 730    # tie-breaks, sets décisifs : 2 ans
BREAK_DAYS = 45      # absence au-delà de laquelle on parle de retour de blessure / de pause
LOCATION_ALIASES = {"toronto": "montreal"}   # l'Open du Canada alterne entre les deux villes


def tournament_key(location: str, surface: str, tier: str) -> str:
    """Identifiant d'un tournoi d'une année sur l'autre (le nom change avec les sponsors, pas la ville)."""
    loc = norm_name(location)
    loc = LOCATION_ALIASES.get(loc, loc)
    return f"{loc}|{surface}|{'slam' if str(tier) == 'Grand Slam' else 'tour'}"


class Rec(NamedTuple):
    d: date
    won: int
    exp: float           # chance de gagner selon l'Elo avant le match
    surface: str
    sw: int              # sets gagnés / perdus
    sl: int
    tbw: int             # tie-breaks gagnés / perdus
    tbl: int
    dec: int             # set décisif : 1 gagné, -1 perdu, 0 pas de set décisif
    first: int           # premier set : 1 gagné, -1 perdu, 0 inconnu
    games: float         # jeux joués dans le match


def set_stats(r) -> dict:
    """Sets, tie-breaks, set décisif et premier set, du point de vue du vainqueur."""
    sw = sl = tbw = tbl = 0
    first = 0
    for i in range(1, 6):
        w, l = getattr(r, f"w{i}", None), getattr(r, f"l{i}", None)
        if w is None or l is None or w != w or l != l:          # NaN : set non joué
            continue
        if w > l:
            sw += 1
        elif l > w:
            sl += 1
        if {w, l} == {7, 6} or (max(w, l) >= 7 and abs(w - l) == 1 and i == r.best_of):   # tie-break, y compris super tie-break final
            tbw += w > l
            tbl += l > w
        if i == 1:
            first = 1 if w > l else -1
    dec = 1 if (sw + sl == r.best_of and sw > sl) else 0
    return {"sw": sw, "sl": sl, "tbw": tbw, "tbl": tbl, "dec": dec, "first": first}


def _rate(w: float, n: float, k: float = 2.0) -> float:
    return (w + k) / (n + 2 * k)


# Fabriques nommées (et non des lambdas) : l'état est enregistré avec pickle dans live_model.pkl.
def _log():
    return deque(maxlen=150)


def _tourn():
    return [0, 0, 0.0]


def _injuries():
    return deque(maxlen=10)


def _ranks():
    return deque(maxlen=40)


class PlayerProfile:
    def __init__(self):
        self.log: dict = defaultdict(_log)            # joueur -> matchs terminés (Rec)
        self.tourn: dict = defaultdict(_tourn)        # (joueur, tournoi) -> [matchs, victoires, surperformance]
        self.injuries: dict = defaultdict(_injuries)  # joueur -> dates d'abandon ou de forfait
        self.ranks: dict = defaultdict(_ranks)        # joueur -> (date, classement)

    # ------------------------------------------------------------------ mise à jour
    def record(self, r, exp_w: float, tkey: str) -> None:
        """Après un match (ligne tennis-data) : `exp_w` = chance du vainqueur selon l'Elo d'avant-match."""
        for key, rank in ((r.winner_key, r.w_rank), (r.loser_key, r.l_rank)):
            if rank == rank and rank is not None:
                self.ranks[key].append((r.date, float(rank)))
        if r.walkover or not r.completed:                  # forfait ou abandon : signal physique pour le perdant
            self.injuries[r.loser_key].append(r.date)
            return
        s = set_stats(r)
        games = float(r.w_games + r.l_games)
        self.log[r.winner_key].append(Rec(r.date, 1, exp_w, r.surface, s["sw"], s["sl"], s["tbw"], s["tbl"],
                                          s["dec"], s["first"], games))
        self.log[r.loser_key].append(Rec(r.date, 0, 1 - exp_w, r.surface, s["sl"], s["sw"], s["tbl"], s["tbw"],
                                         -s["dec"], -s["first"], games))
        for key, won, exp in ((r.winner_key, 1, exp_w), (r.loser_key, 0, 1 - exp_w)):
            t = self.tourn[(key, tkey)]
            t[0] += 1
            t[1] += won
            t[2] += won - exp

    # ------------------------------------------------------------------ lecture
    def stats(self, p: str, d: date, surface: str, tkey: str | None) -> dict:
        """Chiffres bruts du profil (affichés tels quels dans la fiche du joueur)."""
        recs = self.log.get(p, ())
        last10 = list(recs)[-FORM_N:]
        out = {"n10": len(last10), "w10": sum(r.won for r in last10), "surplus10": sum(r.won - r.exp for r in last10),
               "form10": "".join("V" if r.won else "D" for r in reversed(last10))}
        ns = ws = 0
        ss = 0.0
        sw = sl = tbw = tbl = decw = decl = lf = lfw = 0
        for r in recs:
            age = (d - r.d).days
            if age <= SURF_DAYS and r.surface == surface:
                ns += 1
                ws += r.won
                ss += r.won - r.exp
            if age <= MENTAL_DAYS:
                sw, sl, tbw, tbl = sw + r.sw, sl + r.sl, tbw + r.tbw, tbl + r.tbl
                decw += r.dec == 1
                decl += r.dec == -1
                if r.first == -1:
                    lf += 1
                    lfw += r.won
        out.update({"surf_n": ns, "surf_w": ws, "surf_surplus": ss,
                    "sets_w": sw, "sets_l": sl, "tb_w": tbw, "tb_l": tbl, "dec_w": decw, "dec_l": decl,
                    "lost_first": lf, "won_after_lost_first": lfw})
        t = self.tourn.get((p, tkey), (0, 0, 0.0)) if tkey else (0, 0, 0.0)
        out.update({"tourn_n": t[0], "tourn_w": t[1], "tourn_surplus": t[2]})

        # Physique : dernier match (jeux, il y a combien de jours), matchs sur 4 jours, retour de pause
        last = recs[-1] if recs else None
        gap = (d - last.d).days if last else 999
        out["last_games"] = last.games if last and gap <= 3 else 0.0
        out["last_days"] = gap
        out["m4"] = sum(1 for r in recs if (d - r.d).days <= 4)
        since, break_days = 10, 0                           # matchs joués depuis la dernière longue pause
        if last and gap >= BREAK_DAYS:                      # le match à venir est le premier après la pause
            since, break_days = 0, gap
        elif last:
            dates = [r.d for r in recs]
            for i in range(len(dates) - 1, 0, -1):
                if (dates[i] - dates[i - 1]).days >= BREAK_DAYS:
                    if (d - dates[i]).days <= 180:          # au-delà, ce n'est plus un retour
                        since, break_days = min(len(dates) - i, 10), (dates[i] - dates[i - 1]).days
                    break
        out["since_break"], out["break_days"] = since, break_days
        inj = [x for x in self.injuries.get(p, ()) if x <= d]
        out["inj_days"] = (d - inj[-1]).days if inj else 999
        out["inj_365"] = sum(1 for x in inj if (d - x).days <= 365)

        # Classement : maintenant et il y a 3 mois
        rk = self.ranks.get(p, ())
        now_r = rk[-1][1] if rk else None
        past = [x for x in rk if (d - x[0]).days >= 90]
        out["rank_now"], out["rank_3m"] = now_r, (past[-1][1] if past else None)
        return out

    def features(self, p: str, d: date, surface: str, tkey: str | None) -> dict:
        """Variables du modèle (une par facteur), à comparer entre les deux joueurs."""
        s = self.stats(p, d, surface, tkey)
        set_rate = _rate(s["sets_w"], s["sets_w"] + s["sets_l"])
        return {
            "form10": s["surplus10"] / (s["n10"] + 3),
            "surf_form": s["surf_surplus"] / (s["surf_n"] + 5),
            "tourn_form": s["tourn_surplus"] / (s["tourn_n"] + 4),
            "clutch_tb": _rate(s["tb_w"], s["tb_w"] + s["tb_l"]) - set_rate,
            "clutch_dec": _rate(s["dec_w"], s["dec_w"] + s["dec_l"]) - set_rate,
            "comeback": (s["won_after_lost_first"] + 0.5) / (s["lost_first"] + 2) - 0.25,   # ~25 % en moyenne
            "last_games": s["last_games"],
            "m4": s["m4"],
            "rusty": (10 - s["since_break"]) / 10,
            "injury": math.exp(-s["inj_days"] / 30) if s["inj_days"] <= 90 else 0.0,
            "rank_mom": (math.log(s["rank_3m"]) - math.log(s["rank_now"])) if s["rank_now"] and s["rank_3m"] else 0.0,
        }


# Clés de tournoi The Odds API qui ne contiennent pas le nom de la ville.
LIVE_LOCATIONS = {
    "aus_open": "melbourne", "french_open": "paris", "us_open": "new york", "wimbledon": "london",
    "italian": "rome", "canadian": "montreal", "china_open": "beijing", "qatar": "doha",
    "monte_carlo": "monte carlo", "indian_wells": "indian wells", "japan": "tokyo", "pan_pacific": "tokyo",
    "swiss_indoors": "basel", "european_open": "brussels", "atp_finals": "turin", "wta_finals": "riyadh",
}


def live_tournament_key(sport_key: str, surface: str, tier: str, known: set) -> str | None:
    """Tournoi de l'historique correspondant à une clé The Odds API (ex. tennis_atp_paris_masters)."""
    level = "slam" if tier == "Grand Slam" else "tour"
    locs = [loc for kw, loc in LIVE_LOCATIONS.items() if kw in sport_key]
    locs += sorted({k.split("|")[0] for k in known if k.split("|")[0].replace(" ", "_") in sport_key}, key=len, reverse=True)
    for loc in locs:
        key = f"{loc}|{surface}|{level}"
        if key in known:
            return key
    return None
