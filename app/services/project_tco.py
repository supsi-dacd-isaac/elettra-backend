"""Finite-horizon financial projections for fleet designs."""

import math
from numbers import Real


def _nonnegative_number(value: float, label: str) -> float:
    message = f"{label} must be finite and nonnegative"
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(message)
    try:
        value = float(value)
    except OverflowError as exc:
        raise ValueError(message) from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError(message)
    return value


def _capital_component(
    key: str, initial: float, life: float, horizon: int, log_growth: float
) -> dict:
    replacements = []
    last_purchase = 0.0
    for purchase_number in range(1, horizon):
        year = purchase_number * life
        if year >= horizon:
            break
        replacements.append({
            "year": year,
            "cost": initial,
            "discounted": initial * math.exp(-year * log_growth),
        })
        last_purchase = year

    depreciated_share = min(1.0, (horizon - last_purchase) / life)
    if (len(replacements) + 1) * life == horizon:
        depreciated_share = 1.0
    residual = initial * (1.0 - depreciated_share)
    terminal_exponent = -horizon * log_growth
    discounted_residual = residual * math.exp(terminal_exponent)
    discounted_replacements = sum(event["discounted"] for event in replacements)
    # Compute the initial cost less the discounted residual without subtracting
    # nearly equal costs for a long-lived asset at a low discount rate.
    net_initial = initial * depreciated_share - residual * math.expm1(terminal_exponent)
    return {
        "key": key,
        "life": life,
        "initial": initial,
        "replacements": replacements,
        "residual": residual,
        "discountedReplacements": discounted_replacements,
        "discountedResidual": discounted_residual,
        "presentValue": net_initial + discounted_replacements,
    }


def project_design(
    assets: list[tuple[str, float, float]],
    operating: list[tuple[str, float]],
    annual_km: float,
    horizon: int,
    rate: float,
) -> dict:
    """Project asset cash flows and end-of-year operating costs in one design."""
    if isinstance(horizon, bool) or not isinstance(horizon, int) or not 1 <= horizon <= 200:
        raise ValueError("Horizon must be an integer between 1 and 200 years")
    rate = _nonnegative_number(rate, "Discount rate")
    if rate > 1:
        raise ValueError("Discount rate must be at most 1")
    annual_km = _nonnegative_number(annual_km, "Annual distance")
    log_growth = math.log1p(rate)
    annuity_factor = -math.expm1(-horizon * log_growth) / rate if rate else horizon

    components = []
    for key, cost, life in assets:
        if not isinstance(key, str) or not key.strip():
            raise ValueError("Asset key must be a nonempty string")
        cost = _nonnegative_number(cost, f"{key} cost")
        life = _nonnegative_number(life, f"{key} lifetime")
        if life < 1:
            raise ValueError(f"{key} lifetime must be at least 1 year")
        components.append(_capital_component(key, cost, life, horizon, log_growth))

    operating_costs = []
    for key, annual_cost in operating:
        if not isinstance(key, str) or not key.strip():
            raise ValueError("Operating cost key must be a nonempty string")
        annual_cost = _nonnegative_number(annual_cost, f"{key} annual cost")
        operating_costs.append({"key": key, "annual": annual_cost})

    annual_opex = sum(cost["annual"] for cost in operating_costs)
    present_value = (
        sum(component["presentValue"] for component in components)
        + annual_opex * annuity_factor
    )
    annual_equivalent = present_value / annuity_factor
    totals = {
        "initial": sum(component["initial"] for component in components),
        "annualOpex": annual_opex,
        "presentValue": present_value,
        "annuityFactor": annuity_factor,
        "annualEquivalent": annual_equivalent,
        "perKm": annual_equivalent / annual_km if annual_km else None,
        "residual": sum(component["residual"] for component in components),
        "discountedResidual": sum(component["discountedResidual"] for component in components),
        "discountedReplacements": sum(
            component["discountedReplacements"] for component in components
        ),
    }
    if any(value is not None and not math.isfinite(value) for value in totals.values()):
        raise ValueError("Projected cash flows must be finite")
    return {"components": components, "operating": operating_costs, **totals}
