"""Flux estimés à partir des mouvements de cotes.

Les bookmakers ne publient pas les montants misés sur chaque joueur. On les
estime par la réaction des cotes : un bookmaker baisse la cote du joueur sur
lequel l'argent arrive. Pour chaque match et chaque relevé :
- proba juste (sans marge) de chaque bookmaker ;
- consensus français = moyenne des bookmakers français (clé en « _fr ») ;
- référence sharp = moyenne Pinnacle / Betfair Exchange.

Flux = variation du consensus depuis l'ouverture (ou depuis un instant donné),
en points de proba, vers le joueur dont la proba monte. Origine probable :
- « informé » : les cotes sharp ont bougé (argent des parieurs avisés ou
  information nouvelle : blessure, forfait…), les français suivent ou non ;
- « grand public » : seuls les bookmakers français ont bougé (afflux de mises du
  public, ou gestion du risque du bookmaker) ;
- « contradictoire » : français et sharp bougent en sens opposés ;
- « stable » : moins d'un point de mouvement.
C'est une estimation : elle dit où va l'argent, pas combien.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tennisvalue.market.movement import with_fair_probs

THRESHOLD_PP = 1.0   # en dessous d'un point de proba, on parle de cote stable


def is_french(book: str, anj_books: tuple = ()) -> bool:
    return book.endswith("_fr") or book in anj_books


def book_group(books: pd.Series, sharp_books: tuple, anj_books: tuple = ()) -> pd.Series:
    return pd.Series(np.where(books.isin(sharp_books), "sharp",
                              np.where([is_french(b, anj_books) for b in books], "fr", "autre")), index=books.index)


def timeline(snaps: pd.DataFrame, sharp_books: tuple, anj_books: tuple = ()) -> pd.DataFrame:
    """Une ligne par (match, relevé) : proba juste de J1 selon le consensus français et selon le sharp."""
    if snaps.empty:
        return pd.DataFrame(columns=["event_id", "fetched_at", "fr", "sharp"])
    s = with_fair_probs(snaps)
    s["groupe"] = book_group(s["book"], sharp_books, anj_books)
    t = (s[s["groupe"] != "autre"].pivot_table(index=["event_id", "fetched_at"], columns="groupe",
                                               values="fair_1", aggfunc="mean")
         .reindex(columns=["fr", "sharp"]).reset_index())
    t.columns.name = None
    return t.sort_values(["event_id", "fetched_at"]).reset_index(drop=True)


def _origin(d_fr: float, d_sharp: float, thr: float) -> str:
    moved_fr = pd.notna(d_fr) and abs(d_fr) >= thr
    moved_sharp = pd.notna(d_sharp) and abs(d_sharp) >= thr
    if not moved_fr and not moved_sharp:
        return "stable"
    if moved_sharp and moved_fr and np.sign(d_fr) != np.sign(d_sharp):
        return "contradictoire"
    return "informé" if moved_sharp else "grand public"


def summary(snaps: pd.DataFrame, sharp_books: tuple, anj_books: tuple = (), since=None,
            thr: float = THRESHOLD_PP) -> pd.DataFrame:
    """Flux par match : variation du consensus (en points) depuis l'ouverture, ou depuis `since`.

    Colonnes : delta_fr, delta_sharp (points de proba de J1), flux (delta retenu :
    français s'ils ont bougé, sinon sharp), source (« fr » ou « sharp »), vers (1 ou 2,
    0 si stable), origine, n_releves,
    debut / fin (horodatages de la période mesurée).
    """
    cols = ["event_id", "delta_fr", "delta_sharp", "flux", "source", "vers", "origine", "n_releves", "debut", "fin"]
    t = timeline(snaps, sharp_books, anj_books)
    if t.empty:
        return pd.DataFrame(columns=cols)
    rows = []
    for ev, g in t.groupby("event_id"):
        base = g
        if since is not None:
            before = g[g["fetched_at"] <= since]
            base = g.loc[before.index[-1]:] if not before.empty else g
        first, last = base.iloc[0], base.iloc[-1]

        def delta(col):
            v = base[col].dropna()
            return 100 * (v.iloc[-1] - v.iloc[0]) if len(v) >= 2 else (0.0 if len(v) == 1 else np.nan)

        d_fr, d_sharp = delta("fr"), delta("sharp")
        # Mouvement français s'il y en a un ; sinon mouvement sharp (argent informé que les
        # bookmakers français n'ont pas encore suivi : leurs cotes sont en retard).
        moved_fr = pd.notna(d_fr) and abs(d_fr) >= thr
        source = "fr" if moved_fr or pd.isna(d_sharp) else "sharp"
        flux = d_fr if source == "fr" else d_sharp
        flux = 0.0 if pd.isna(flux) else float(flux)
        rows.append({"event_id": ev, "delta_fr": d_fr, "delta_sharp": d_sharp, "flux": flux, "source": source,
                     "vers": 0 if abs(flux) < thr else (1 if flux > 0 else 2),
                     "origine": _origin(d_fr, d_sharp, thr), "n_releves": len(base),
                     "debut": first["fetched_at"], "fin": last["fetched_at"]})
    return pd.DataFrame(rows, columns=cols)


def best_odds(snaps: pd.DataFrame, anj_books: tuple = ()) -> pd.DataFrame:
    """Meilleures cotes françaises de chaque joueur, à l'ouverture et au dernier relevé, par match."""
    cols = ["event_id", "cote_1", "book_1", "cote_2", "book_2", "ouv_1", "ouv_2"]
    fr = snaps[[is_french(b, anj_books) for b in snaps["book"]]]
    if fr.empty:
        return pd.DataFrame(columns=cols)
    fr = fr.sort_values("fetched_at")
    latest = fr.groupby(["event_id", "book"]).tail(1)
    opening = fr[fr["fetched_at"] == fr.groupby("event_id")["fetched_at"].transform("min")]
    rows = []
    for ev, g in latest.groupby("event_id"):
        o = opening[opening["event_id"] == ev]
        i1, i2 = g["odds_1"].idxmax(), g["odds_2"].idxmax()
        rows.append({"event_id": ev, "cote_1": g.at[i1, "odds_1"], "book_1": g.at[i1, "book"],
                     "cote_2": g.at[i2, "odds_2"], "book_2": g.at[i2, "book"],
                     "ouv_1": o["odds_1"].max(), "ouv_2": o["odds_2"].max()})
    return pd.DataFrame(rows, columns=cols)
