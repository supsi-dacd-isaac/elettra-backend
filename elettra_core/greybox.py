"""Stable grey-box/QRF model symbols shared by training and inference.

Model artifacts must reference this module rather than an executable trainer's
``__main__`` namespace.  The classes intentionally depend only on NumPy and
Pandas at inference time; SciPy is imported lazily by ``fit``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd


GREYBOX_PRED_FEATURE = "greybox_pred_kwh"
GREYBOX_REQUIRED_COLUMNS = (
    "bus_length_m",
    "bus_battery_kwh",
    "total_distance_m",
    "driving_average_speed_kmh",
    "total_ascent_m",
    "total_descent_m",
    "driving_time_minutes",
    "total_duration_minutes",
    "pct_downhill_segments",
)


def _vector(value: Any, *, rows: int, name: str) -> np.ndarray:
    result = np.asarray(value, dtype=float).reshape(-1)
    if len(result) != rows:
        raise ValueError(f"{name} has {len(result)} rows; expected {rows}")
    if not np.isfinite(result).all():
        raise ValueError(f"{name} contains non-finite values")
    return result


@dataclass(frozen=True)
class AuxiliaryEnergyComponents:
    """Per-row energy supplied by an external auxiliary estimator."""

    hvac_electrical_kwh: np.ndarray
    fixed_auxiliary_kwh: np.ndarray
    diesel_fuel_kwh: np.ndarray
    diesel_liters: np.ndarray
    uncovered_thermal_kwh: np.ndarray

    @classmethod
    def zeros(cls, rows: int) -> "AuxiliaryEnergyComponents":
        zero = np.zeros(rows, dtype=float)
        return cls(zero.copy(), zero.copy(), zero.copy(), zero.copy(), zero.copy())

    @classmethod
    def coerce(cls, value: Any, *, rows: int) -> "AuxiliaryEnergyComponents":
        if value is None:
            return cls.zeros(rows)
        if isinstance(value, cls):
            result = value
        elif isinstance(value, Mapping):
            result = cls(
                hvac_electrical_kwh=value.get("hvac_electrical_kwh", np.zeros(rows)),
                fixed_auxiliary_kwh=value.get("fixed_auxiliary_kwh", np.zeros(rows)),
                diesel_fuel_kwh=value.get("diesel_fuel_kwh", np.zeros(rows)),
                diesel_liters=value.get("diesel_liters", np.zeros(rows)),
                uncovered_thermal_kwh=value.get("uncovered_thermal_kwh", np.zeros(rows)),
            )
        else:
            # A numeric legacy callable represents an undifferentiated battery
            # auxiliary component. Treat it as HVAC only for compatibility.
            result = cls(
                hvac_electrical_kwh=value,
                fixed_auxiliary_kwh=np.zeros(rows),
                diesel_fuel_kwh=np.zeros(rows),
                diesel_liters=np.zeros(rows),
                uncovered_thermal_kwh=np.zeros(rows),
            )
        normalized = cls(
            hvac_electrical_kwh=_vector(
                result.hvac_electrical_kwh, rows=rows, name="hvac_electrical_kwh"
            ),
            fixed_auxiliary_kwh=_vector(
                result.fixed_auxiliary_kwh, rows=rows, name="fixed_auxiliary_kwh"
            ),
            diesel_fuel_kwh=_vector(
                result.diesel_fuel_kwh, rows=rows, name="diesel_fuel_kwh"
            ),
            diesel_liters=_vector(result.diesel_liters, rows=rows, name="diesel_liters"),
            uncovered_thermal_kwh=_vector(
                result.uncovered_thermal_kwh, rows=rows, name="uncovered_thermal_kwh"
            ),
        )
        for name in (
            "hvac_electrical_kwh",
            "fixed_auxiliary_kwh",
            "diesel_fuel_kwh",
            "diesel_liters",
            "uncovered_thermal_kwh",
        ):
            if (getattr(normalized, name) < 0).any():
                raise ValueError(f"{name} must be non-negative")
        return normalized

    @property
    def battery_auxiliary_kwh(self) -> np.ndarray:
        return self.hvac_electrical_kwh + self.fixed_auxiliary_kwh


class _GreyBoxBase:
    variant = "base"

    def __init__(
        self,
        *,
        battery_pack_density: float = 6.85,
        ridge_alpha: float = 0.0,
        fit_loss: str = "linear",
        fit_f_scale: float = 1.0,
    ) -> None:
        if not math.isfinite(battery_pack_density) or battery_pack_density <= 0:
            raise ValueError("battery_pack_density must be finite and positive")
        if not math.isfinite(ridge_alpha) or ridge_alpha < 0:
            raise ValueError("ridge_alpha must be finite and non-negative")
        if fit_loss not in {"linear", "huber", "soft_l1"}:
            raise ValueError("fit_loss must be linear, huber or soft_l1")
        if not math.isfinite(fit_f_scale) or fit_f_scale <= 0:
            raise ValueError("fit_f_scale must be finite and positive")
        self.battery_pack_density = float(battery_pack_density)
        self.ridge_alpha = float(ridge_alpha)
        self.fit_loss = fit_loss
        self.fit_f_scale = float(fit_f_scale)
        self.theta_: np.ndarray | None = None
        self.fit_diagnostics_: dict[str, Any] = {}

    @staticmethod
    def _arrays(X: pd.DataFrame) -> dict[str, np.ndarray]:
        missing = set(GREYBOX_REQUIRED_COLUMNS) - set(X.columns)
        if missing:
            raise ValueError(f"Missing Grey Box inputs: {sorted(missing)}")
        arrays = {
            "length": X["bus_length_m"].to_numpy(dtype=float),
            "battery": X["bus_battery_kwh"].to_numpy(dtype=float),
            "distance": X["total_distance_m"].to_numpy(dtype=float),
            "speed": X["driving_average_speed_kmh"].to_numpy(dtype=float) / 3.6,
            "ascent": X["total_ascent_m"].to_numpy(dtype=float),
            "descent": X["total_descent_m"].to_numpy(dtype=float),
            "driving_minutes": X["driving_time_minutes"].to_numpy(dtype=float),
            "duration_minutes": X["total_duration_minutes"].to_numpy(dtype=float),
            "pct_downhill": X["pct_downhill_segments"].to_numpy(dtype=float),
        }
        if not np.isfinite(np.column_stack(tuple(arrays.values()))).all():
            raise ValueError("Grey Box inputs contain non-finite values")
        if (arrays["duration_minutes"] <= 0).any():
            raise ValueError("Trip duration must be positive")
        return arrays

    def parameter_names(self) -> tuple[str, ...]:
        raise NotImplementedError

    def initial_bounds(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        raise NotImplementedError

    def _component_prediction(
        self,
        arrays: Mapping[str, np.ndarray],
        theta: np.ndarray,
        override_mass: np.ndarray | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        raise NotImplementedError

    def fit(self, X: pd.DataFrame, y: pd.Series) -> "_GreyBoxBase":
        from scipy.optimize import least_squares

        arrays = self._arrays(X)
        target = y.to_numpy(dtype=float)
        if len(target) != len(X) or not np.isfinite(target).all():
            raise ValueError("Grey Box target must be finite and row-aligned")
        initial, lower, upper = self.initial_bounds()
        prior_scale = np.abs(initial).copy()
        finite_zero = (prior_scale == 0) & np.isfinite(lower) & np.isfinite(upper)
        prior_scale[finite_zero] = (upper[finite_zero] - lower[finite_zero]) / 4.0
        prior_scale[prior_scale == 0] = 1.0
        penalty = math.sqrt(len(target) * self.ridge_alpha / len(initial))

        def residual(theta_scaled: np.ndarray) -> np.ndarray:
            theta = theta_scaled * prior_scale
            mechanical, fixed = self._component_prediction(arrays, theta)
            data_residual = mechanical + fixed - target
            if self.ridge_alpha == 0:
                return data_residual
            return np.concatenate(
                [data_residual, penalty * (theta - initial) / prior_scale]
            )

        fit = least_squares(
            residual,
            initial / prior_scale,
            bounds=(lower / prior_scale, upper / prior_scale),
            method="trf",
            loss=self.fit_loss,
            f_scale=self.fit_f_scale,
        )
        if not fit.success:
            raise RuntimeError(f"{self.variant} fit failed: {fit.message}")
        self.theta_ = fit.x * prior_scale
        mechanical, fixed = self._component_prediction(arrays, self.theta_)
        data_residual = mechanical + fixed - target
        self.fit_diagnostics_ = {
            "loss": self.fit_loss,
            "f_scale_kwh": self.fit_f_scale,
            "ridge_alpha": self.ridge_alpha,
            "ridge_prior": "physical_initial_parameters",
            "nfev": int(fit.nfev),
            "data_mae_kwh": float(np.mean(np.abs(data_residual))),
            "data_rmse_kwh": float(np.sqrt(np.mean(data_residual**2))),
        }
        return self

    def _require_theta(self) -> np.ndarray:
        if self.theta_ is None:
            raise RuntimeError("Grey Box has not been fitted")
        return self.theta_

    def predict_components(
        self, X: pd.DataFrame, *, override_mass: np.ndarray | None = None
    ) -> tuple[np.ndarray, np.ndarray]:
        arrays = self._arrays(X)
        mass = None
        if override_mass is not None:
            mass = _vector(override_mass, rows=len(X), name="override_mass")
            if (mass <= 0).any():
                raise ValueError("override_mass must be positive")
        return self._component_prediction(arrays, self._require_theta(), mass)

    def predict(
        self, X: pd.DataFrame, *, override_mass: np.ndarray | None = None
    ) -> np.ndarray:
        mechanical, fixed = self.predict_components(X, override_mass=override_mass)
        return mechanical + fixed

    def get_params_dict(self) -> dict[str, float]:
        if self.theta_ is None:
            return {}
        return {
            **{
                name: float(value)
                for name, value in zip(self.parameter_names(), self.theta_)
            },
            "battery_pack_density": self.battery_pack_density,
        }

    def get_fit_diagnostics(self) -> dict[str, Any]:
        return dict(self.fit_diagnostics_)

    def _common_terms(
        self,
        arrays: Mapping[str, np.ndarray],
        *,
        alpha_roll: float,
        alpha_aero: float,
        alpha_up: float,
        k1: float,
        k2: float,
        override_mass: np.ndarray | None,
    ) -> tuple[np.ndarray, np.ndarray]:
        mass = (
            override_mass
            if override_mass is not None
            else self.battery_pack_density * arrays["battery"]
            + k1 * arrays["length"]
            + k2
        )
        moving_fraction = arrays["driving_minutes"] / arrays["duration_minutes"]
        positive = (
            alpha_roll * mass * arrays["distance"]
            + alpha_aero * arrays["distance"] * arrays["speed"] ** 2 * moving_fraction
            + alpha_up * mass * arrays["ascent"]
        )
        return mass, positive


class LinearGreyBox(_GreyBoxBase):
    """G0 mechanics with an independent non-positive downhill coefficient."""

    variant = "G0-linear"

    def parameter_names(self) -> tuple[str, ...]:
        return ("alpha_roll", "alpha_aero", "alpha_up", "alpha_down", "k1", "k2")

    def initial_bounds(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        return (
            np.array([1e-6, 1e-9, 1e-6, -1e-6, 1e-3, 0.0]),
            np.array([0.0, 0.0, 0.0, -np.inf, 0.0, 0.0]),
            np.array([np.inf, np.inf, np.inf, 0.0, np.inf, np.inf]),
        )

    def _component_prediction(self, arrays, theta, override_mass=None):
        alpha_roll, alpha_aero, alpha_up, alpha_down, k1, k2 = theta
        mass, positive = self._common_terms(
            arrays,
            alpha_roll=alpha_roll,
            alpha_aero=alpha_aero,
            alpha_up=alpha_up,
            k1=k1,
            k2=k2,
            override_mass=override_mass,
        )
        return positive + alpha_down * mass * arrays["descent"], np.zeros(len(mass))


class CappedRegenAffineGreyBox(_GreyBoxBase):
    """G2 mechanics with linked/capped regen and an affine fixed load."""

    variant = "G2-capped-regen-affine-fixed"

    def parameter_names(self) -> tuple[str, ...]:
        return (
            "alpha_roll",
            "alpha_aero",
            "alpha_up",
            "regen_ratio",
            "regen_c_rate",
            "k1",
            "k2",
            "fixed_power_intercept_raw",
            "fixed_power_length_slope",
        )

    def initial_bounds(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        return (
            np.array([2.2e-7, 4.2e-12, 1.27e-5, 0.73, 0.5, 82.0, 30.0, 2.4143495, 0.05]),
            np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, -20.0, 0.0]),
            np.array([np.inf, np.inf, np.inf, 1.0, 1.5, np.inf, np.inf, 20.0, 2.0]),
        )

    @staticmethod
    def _fixed_power(length: np.ndarray, intercept: float, slope: float) -> np.ndarray:
        return np.logaddexp(0.0, intercept + slope * (length - 12.0))

    def _component_prediction(self, arrays, theta, override_mass=None):
        (
            alpha_roll,
            alpha_aero,
            alpha_up,
            regen_ratio,
            regen_c_rate,
            k1,
            k2,
            fixed_intercept,
            fixed_slope,
        ) = theta
        mass, positive = self._common_terms(
            arrays,
            alpha_roll=alpha_roll,
            alpha_aero=alpha_aero,
            alpha_up=alpha_up,
            k1=k1,
            k2=k2,
            override_mass=override_mass,
        )
        potential = regen_ratio * alpha_up * mass * arrays["descent"]
        downhill_hours = (
            arrays["driving_minutes"]
            / 60.0
            * np.clip(arrays["pct_downhill"], 0.0, 100.0)
            / 100.0
        )
        recovery = np.minimum(
            potential, regen_c_rate * arrays["battery"] * downhill_hours
        )
        fixed_power = self._fixed_power(arrays["length"], fixed_intercept, fixed_slope)
        fixed_energy = fixed_power * arrays["duration_minutes"] / 60.0
        return positive - recovery, fixed_energy

    def fixed_power_kw(self, X: pd.DataFrame) -> np.ndarray:
        theta = self._require_theta()
        return self._fixed_power(
            X["bus_length_m"].to_numpy(dtype=float), theta[-2], theta[-1]
        )

    def get_params_dict(self) -> dict[str, float]:
        values = super().get_params_dict()
        if self.theta_ is not None:
            reference = pd.DataFrame({"bus_length_m": [9.0, 10.0, 10.8, 12.0, 13.0, 18.0]})
            for length, power in zip(
                reference["bus_length_m"], self.fixed_power_kw(reference), strict=True
            ):
                values[f"derived_fixed_power_{str(length).replace('.', '_')}m_kw"] = float(power)
        return values


@dataclass(frozen=True)
class PredictionComponents:
    total_kwh: np.ndarray
    mechanical_greybox_kwh: np.ndarray
    qrf_residual_kwh: np.ndarray
    fixed_auxiliary_kwh: np.ndarray
    hvac_electrical_kwh: np.ndarray
    diesel_fuel_kwh: np.ndarray
    diesel_liters: np.ndarray
    uncovered_thermal_kwh: np.ndarray

    @property
    def auxiliary_kwh(self) -> np.ndarray:
        return self.fixed_auxiliary_kwh + self.hvac_electrical_kwh

    @property
    def drivetrain_kwh(self) -> np.ndarray:
        if self.qrf_residual_kwh.ndim == 2:
            return self.mechanical_greybox_kwh[:, None] + self.qrf_residual_kwh
        return self.mechanical_greybox_kwh + self.qrf_residual_kwh


class HybridGreyboxQRF:
    """Stable G0/G2 + residual QRF artifact with explicit components."""

    model_type = "HybridGreyboxQRF"

    def __init__(
        self,
        *,
        greybox: _GreyBoxBase,
        qrf: Any,
        selected_features: Sequence[str],
        prediction_stack: str,
        qrf_reference_occupancy_percent: float | None = None,
    ) -> None:
        if prediction_stack not in {"vecto-g2", "vecto-g0-transfer"}:
            raise ValueError("HybridGreyboxQRF requires a VECTO prediction stack")
        if prediction_stack == "vecto-g2" and not isinstance(
            greybox, CappedRegenAffineGreyBox
        ):
            raise ValueError("vecto-g2 requires CappedRegenAffineGreyBox")
        if prediction_stack == "vecto-g0-transfer" and not isinstance(
            greybox, LinearGreyBox
        ):
            raise ValueError("vecto-g0-transfer requires LinearGreyBox")
        self.greybox = greybox
        self.qrf = qrf
        self.selected_features = list(selected_features)
        self.prediction_stack = prediction_stack
        if qrf_reference_occupancy_percent is None:
            self.qrf_reference_occupancy_percent = None
        else:
            reference = float(qrf_reference_occupancy_percent)
            if (
                isinstance(qrf_reference_occupancy_percent, bool)
                or not math.isfinite(reference)
                or not 0 <= reference <= 100
            ):
                raise ValueError(
                    "qrf_reference_occupancy_percent must be finite and between 0 and 100"
                )
            self.qrf_reference_occupancy_percent = reference

    def _qrf_frame(self, X: pd.DataFrame, greybox_total: np.ndarray) -> pd.DataFrame:
        frame = X.drop(
            columns=[column for column in ("bus_number", "bus_battery_kwh") if column in X],
            errors="ignore",
        ).copy()
        if GREYBOX_PRED_FEATURE in self.selected_features:
            frame[GREYBOX_PRED_FEATURE] = greybox_total
        missing = set(self.selected_features) - set(frame.columns)
        if missing:
            raise ValueError(f"Missing QRF inputs: {sorted(missing)}")
        return frame[self.selected_features]

    def _external_auxiliary(
        self,
        X: pd.DataFrame,
        aux_energy_fn: Callable[[pd.DataFrame], Any] | None,
    ) -> AuxiliaryEnergyComponents:
        raw = aux_energy_fn(X) if aux_energy_fn is not None else None
        result = AuxiliaryEnergyComponents.coerce(raw, rows=len(X))
        if self.prediction_stack == "vecto-g2" and np.any(
            result.fixed_auxiliary_kwh != 0
        ):
            raise ValueError("vecto-g2 forbids external fixed auxiliary energy")
        if self.prediction_stack == "vecto-g0-transfer" and np.any(
            result.fixed_auxiliary_kwh <= 0
        ):
            raise ValueError("vecto-g0-transfer requires template fixed auxiliary energy")
        return result

    def predict_components(
        self,
        X: pd.DataFrame,
        *,
        aux_energy_fn: Callable[[pd.DataFrame], Any] | None,
        override_mass: np.ndarray | None = None,
        qrf_reference_mass: np.ndarray | None = None,
        quantiles: str | Sequence[float] | None = "mean",
    ) -> PredictionComponents:
        mechanical, fixed_internal = self.greybox.predict_components(
            X, override_mass=override_mass
        )
        external = self._external_auxiliary(X, aux_energy_fn)
        fixed = fixed_internal + external.fixed_auxiliary_kwh
        if self.qrf_reference_occupancy_percent is None:
            if qrf_reference_mass is not None:
                raise ValueError(
                    "qrf_reference_mass cannot be used by an unanchored model"
                )
            qrf_greybox_total = mechanical + fixed_internal
        else:
            if qrf_reference_mass is None:
                raise ValueError(
                    "anchored QRF models require qrf_reference_mass"
                )
            reference_mechanical, reference_fixed = self.greybox.predict_components(
                X,
                override_mass=qrf_reference_mass,
            )
            qrf_greybox_total = reference_mechanical + reference_fixed
        qrf_frame = self._qrf_frame(X, qrf_greybox_total)
        qrf_residual = np.asarray(
            self.qrf.predict(qrf_frame, quantiles=quantiles)
            if quantiles is not None
            else self.qrf.predict(qrf_frame),
            dtype=float,
        )
        deterministic = mechanical + fixed + external.hvac_electrical_kwh
        total = (
            deterministic[:, None] + qrf_residual
            if qrf_residual.ndim == 2
            else deterministic + qrf_residual.reshape(-1)
        )
        return PredictionComponents(
            total_kwh=total,
            mechanical_greybox_kwh=mechanical,
            qrf_residual_kwh=qrf_residual,
            fixed_auxiliary_kwh=fixed,
            hvac_electrical_kwh=external.hvac_electrical_kwh,
            diesel_fuel_kwh=external.diesel_fuel_kwh,
            diesel_liters=external.diesel_liters,
            uncovered_thermal_kwh=external.uncovered_thermal_kwh,
        )

    def predict(
        self,
        X: pd.DataFrame,
        quantiles: str | Sequence[float] | None = None,
        aux_energy_fn: Callable[[pd.DataFrame], Any] | None = None,
        override_mass: np.ndarray | None = None,
        qrf_reference_mass: np.ndarray | None = None,
    ) -> np.ndarray:
        return self.predict_components(
            X,
            aux_energy_fn=aux_energy_fn,
            override_mass=override_mass,
            qrf_reference_mass=qrf_reference_mass,
            quantiles=quantiles,
        ).total_kwh
