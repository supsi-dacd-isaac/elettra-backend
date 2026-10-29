"""Aggregate trip uncertainty without aligning every trip's upper tail.

The profile represents a percentile of total shift energy, not a simultaneous
95% SOC envelope or a fleet reliability guarantee. Sampling never invokes a
solver and keeps repeated trip occurrences independent.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

import numpy as np
from scipy.stats import binom

METHOD_VERSION = "independent-trip-q95-conditional-v1"


class PredictiveDistributionUnavailable(ValueError):
    """The complete predictive distribution cannot be recovered faithfully."""


def shift_seed(seed: int, shift_id: str) -> int:
    digest = hashlib.sha256(str(shift_id).encode()).digest()
    return (int(seed) + int.from_bytes(digest[:4], "little")) % (2**32)


@dataclass(frozen=True)
class AggregateOptions:
    samples: int = 100_000
    max_samples: int = 500_000
    seed: int = 20261029
    precision_kwh: float = 0.25
    capacity_step_kwh: float | None = None

    @classmethod
    def from_params(cls, params):
        return cls(samples=int(params.get("aggregate_sample_count", 100_000)),
                   max_samples=int(params.get("aggregate_max_samples", 500_000)),
                   seed=int(params.get("aggregate_seed", 20261029)),
                   precision_kwh=float(params.get("aggregate_precision_kwh", 0.25)))


def empirical_qrf_support(predictor, features, **prediction_kwargs):
    """Recover the exact equal-weight forest support, using the loaded model.

    quantile-forest's max_samples_leaf=1 distribution contains one atom per
    estimator. Requesting its order-statistic ranks recovers those atoms through
    the public prediction API, including the physical and auxiliary model.
    Other forest weighting schemes must supply a dedicated distribution adapter;
    interpolating the three persisted quantiles would not be a valid substitute.
    """
    model = predictor.model
    forest = getattr(model, "qrf", model)
    if getattr(forest, "max_samples_leaf", None) != 1:
        raise PredictiveDistributionUnavailable(
            "Aggregate Q95 requires a supported complete predictive distribution; "
            "this model does not expose the equal-weight QRF leaf distribution."
        )
    count = len(getattr(forest, "estimators_", []))
    if count < 1:
        raise PredictiveDistributionUnavailable("The QRF model has no fitted estimators")
    frame = features.drop(columns=["trip_id"], errors="ignore")
    ranks = np.linspace(0, 1, count).tolist() if count > 1 else [0.5]
    if predictor.is_hybrid_greybox:
        def predict(q):
            return model.predict_components(frame, quantiles=q, **prediction_kwargs).total_kwh
    elif predictor.is_greybox:
        kwargs = {k: v for k, v in prediction_kwargs.items() if k != "qrf_reference_mass"}
        def predict(q):
            return model.predict(frame, quantiles=q, **kwargs)
    else:
        def predict(q):
            return model.predict(frame, quantiles=q)
    support = np.asarray(predict(ranks), dtype=float).reshape(len(frame), count)
    mean = np.asarray(predict("mean"), dtype=float).reshape(len(frame))
    if not np.isfinite(support).all() or not np.isfinite(mean).all():
        raise PredictiveDistributionUnavailable("Predictive distribution contains non-finite energy")
    if np.any(np.diff(support, axis=1) < -1e-8) or not np.allclose(
            support.mean(axis=1), mean, atol=1e-7, rtol=1e-8):
        raise PredictiveDistributionUnavailable(
            "Recovered distribution does not reproduce the model mean/order statistics"
        )
    return support, mean, {"distribution": "exact_equal_weight_qrf_leaves", "support_points": count}


def aggregate_profile(support, *, options=AggregateOptions(), seed=None):
    """Two-pass bounded-memory Monte Carlo, with deterministic precision refinement."""
    started = time.monotonic()
    values = np.asarray(support, dtype=float)
    if values.ndim != 2 or not all(values.shape) or not np.isfinite(values).all():
        raise ValueError("A finite non-empty occurrence-by-support matrix is required")
    if not (100 <= options.samples <= options.max_samples <= 1_000_000):
        raise ValueError("Invalid aggregate sample count/limit")
    if not np.isfinite(options.precision_kwh) or options.precision_kwh <= 0:
        raise ValueError("Aggregate precision must be positive")
    actual_seed = options.seed if seed is None else int(seed)
    n_trips, n_atoms = values.shape
    batch_size = max(1, min(25_000, 2_000_000 // n_trips))

    def draws(n):
        rng = np.random.default_rng(actual_seed)
        for first in range(0, n, batch_size):
            count = min(batch_size, n-first)
            indices = rng.integers(n_atoms, size=(count, n_trips))
            yield first, values[np.arange(n_trips)[None, :], indices]

    n = options.samples
    refinement = []
    while True:
        totals = np.empty(n)
        for first, energies in draws(n):
            totals[first:first+len(energies)] = energies.sum(axis=1)
        ordered = np.sort(totals)
        q95 = float(np.quantile(ordered, .95))
        ranks = np.clip(binom.ppf([.025, .975], n, .95).astype(int), 0, n-1)
        interval = ordered[ranks]
        halfwidth = float((interval[1]-interval[0])/2)
        step = options.capacity_step_kwh
        threshold_resolved = (None if not step else
                              bool(np.floor(interval[0]/step) == np.floor(interval[1]/step)))
        refinement.append({"samples": n, "q95_kwh": q95, "ci95_kwh": interval.tolist()})
        if (halfwidth <= options.precision_kwh and threshold_resolved is not False) or n == options.max_samples:
            break
        n = min(2*n, options.max_samples)

    low, high = np.quantile(ordered, [.945, .955])
    profile_sum = np.zeros(n_trips)
    conditional_count = 0
    for _, energies in draws(n):
        total = energies.sum(axis=1)
        keep = (total >= low) & (total <= high)
        profile_sum += energies[keep].sum(axis=0)
        conditional_count += int(keep.sum())
    if not conditional_count:
        raise ValueError("No samples in the aggregate Q95 conditional band")
    profile = profile_sum/conditional_count
    before = float(profile.sum())
    variance = values.var(axis=1)
    weights = variance/variance.sum() if variance.sum() > 0 else np.full(n_trips, 1/n_trips)
    profile += (q95-before)*weights
    mean = values.mean(axis=1)
    return {
        "mean": mean.tolist(), "aggregate_q95": profile.tolist(),
        "statistics": {
            "method_version": METHOD_VERSION, "quantile": .95,
            "independence_assumption": "independent_conditional_errors_per_trip_occurrence",
            "samples": n, "requested_samples": options.samples, "max_samples": options.max_samples,
            "seed": actual_seed, "request_seed": options.seed,
            "trip_occurrences": n_trips, "support_points": n_atoms,
            "mean_total_kwh": float(mean.sum()), "q95_total_kwh": q95,
            "q95_monte_carlo_ci95_kwh": interval.tolist(),
            "precision_target_kwh": options.precision_kwh,
            "precision_met": halfwidth <= options.precision_kwh,
            "capacity_step_kwh": step, "capacity_threshold_resolved": threshold_resolved,
            "refinement": refinement, "conditional_percentiles": [.945, .955],
            "conditional_band_kwh": [float(low), float(high)],
            "conditional_samples": conditional_count,
            "profile_total_before_reconciliation_kwh": before,
            "reconciliation": "variance_weighted_additive",
            "trajectory_coverage_probability": None,
            "elapsed_seconds": time.monotonic()-started,
        },
    }
