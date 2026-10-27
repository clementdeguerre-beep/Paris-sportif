"""Modèle d'ensemble : combine Elo, note aux points, classement, fatigue, etc.

Deux combinateurs, évalués côte à côte en walk-forward :
- « logit » : régression logistique régularisée (L2). Simple, stable, et ses
  coefficients s'interprètent (poids de chaque facteur). Modèle par défaut.
- « gbm »   : gradient boosting (HistGradientBoostingClassifier de scikit-learn),
  capable de capter des effets non linéaires et des interactions.

Symétrie : toutes les variables sont des différences « joueur 1 - joueur 2 ».
On entraîne sur les données doublées (chaque match vu dans les deux sens), sans
constante, pour garantir P(A bat B) = 1 - P(B bat A).

Walk-forward : pour prédire la saison S, on entraîne uniquement sur les saisons
TRAIN_START .. S-1. Le modèle ne voit jamais le futur.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from tennisvalue.models.features import FEATURES

TRAIN_START = 2017  # 2016 = chauffe des notes Elo / points (départ à froid)


def _mirror(X: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return np.vstack([X, -X]), np.concatenate([y, 1 - y])


def make_model(kind: str):
    if kind == "logit":
        return make_pipeline(
            StandardScaler(with_mean=False),
            LogisticRegression(C=1.0, fit_intercept=False, max_iter=2000),
        )
    if kind == "gbm":
        return HistGradientBoostingClassifier(
            max_iter=300, learning_rate=0.04, max_depth=3, min_samples_leaf=200,
            l2_regularization=1.0, random_state=0,
        )
    raise ValueError(kind)


def fit(train: pd.DataFrame, kind: str = "logit"):
    X = train[FEATURES].to_numpy(float)
    y = train["y"].to_numpy(int)
    Xm, ym = _mirror(X, y)
    model = make_model(kind)
    model.fit(Xm, ym)
    return model


def predict(model, df: pd.DataFrame) -> np.ndarray:
    X = df[FEATURES].to_numpy(float)
    p = model.predict_proba(X)[:, 1]
    p_rev = model.predict_proba(-X)[:, 1]
    return 0.5 * (p + (1 - p_rev))  # symétrisation exacte


def walk_forward(feats: pd.DataFrame, test_seasons: list[int], kind: str = "logit") -> pd.Series:
    """Prédictions hors échantillon, saison par saison et circuit par circuit."""
    out = pd.Series(np.nan, index=feats.index, name=f"p_{kind}")
    for tour, g in feats.groupby("tour"):
        for season in test_seasons:
            train = g[(g.season >= TRAIN_START) & (g.season < season) & g.completed]
            test = g[(g.season == season) & ~g.walkover]
            if len(train) < 1000 or test.empty:
                continue
            model = fit(train, kind)
            out.loc[test.index] = predict(model, test)
    return out


def coefficients(model) -> pd.Series:
    """Poids standardisés de la régression logistique (lecture des facteurs clés)."""
    lr = model.named_steps["logisticregression"]
    return pd.Series(lr.coef_[0], index=FEATURES)
