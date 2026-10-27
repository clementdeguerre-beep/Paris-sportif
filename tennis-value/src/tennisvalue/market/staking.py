"""Value et mise : espérance, Kelly fractionné, plafonds de bankroll.

Value (espérance de gain par euro misé) : EV = p x cote - 1.
Kelly (fraction de bankroll qui maximise la croissance à long terme) :
    f* = (p x cote - 1) / (cote - 1)
Kelly plein est très agressif et suppose une probabilité p exacte ; or p est
une estimation bruitée. On mise donc une fraction de Kelly (1/4 par défaut),
avec un plafond par pari et un plafond d'exposition simultanée.
"""
from __future__ import annotations

from dataclasses import dataclass


def expected_value(p: float, odds: float) -> float:
    return p * odds - 1.0


def kelly_fraction(p: float, odds: float) -> float:
    """Fraction de Kelly plein ; 0 si l'espérance est négative."""
    if odds <= 1.0:
        return 0.0
    f = (p * odds - 1.0) / (odds - 1.0)
    return max(f, 0.0)


@dataclass(frozen=True)
class StakingRules:
    kelly_multiplier: float = 0.25   # Kelly fractionné (1/4)
    max_stake_pct: float = 0.02      # plafond par pari : 2 % de la bankroll
    max_exposure_pct: float = 0.10   # plafond des mises ouvertes simultanément : 10 %
    min_stake: float = 1.0           # mise minimale acceptée par les bookmakers (en €)


def recommended_stake(p: float, odds: float, bankroll: float, rules: StakingRules = StakingRules()) -> float:
    """Mise conseillée en euros pour un pari isolé."""
    f = kelly_fraction(p, odds) * rules.kelly_multiplier
    f = min(f, rules.max_stake_pct)
    stake = round(bankroll * f, 2)
    return stake if stake >= rules.min_stake else 0.0


def scale_to_exposure(stakes: list[float], bankroll: float, rules: StakingRules = StakingRules()) -> list[float]:
    """Réduit proportionnellement des mises simultanées si leur total dépasse le plafond d'exposition."""
    total = sum(stakes)
    cap = bankroll * rules.max_exposure_pct
    if total <= cap or total == 0:
        return stakes
    k = cap / total
    return [round(s * k, 2) for s in stakes]


def shrink_to_market(p_model: float, p_market: float, weight_model: float) -> float:
    """Combine la proba du modèle avec celle du marché sharp (option prudente).

    weight_model = 1 : on fait entièrement confiance au modèle ; 0 : au marché.
    """
    return weight_model * p_model + (1 - weight_model) * p_market
