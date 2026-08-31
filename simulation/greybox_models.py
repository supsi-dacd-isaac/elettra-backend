from dataclasses import dataclass
from typing import Optional, Dict, Tuple, List, Callable, Union

import numpy as np
import pandas as pd
from quantile_forest import RandomForestQuantileRegressor


# Greybox required columns
REQUIRED_COLS = [
    "bus_length_m",
    "bus_battery_kwh",
    "total_distance_m",
    "driving_average_speed_kmh",
    "total_ascent_m",
    "total_descent_m",
    "driving_time_minutes",
    "total_duration_minutes",
]

GREYBOX_PRED_FEATURE = "greybox_pred_kwh"
SOLARIS_LENGTHS_M = np.array([9.0, 12.0, 18.0], dtype=float)
SOLARIS_PASSENGER_CAPACITIES = np.array(
    [3588.0 / 68.0, 5258.0 / 68.0, 8889.0 / 68.0], dtype=float
)


@dataclass
class GreyBoxParams:
    alpha_roll: float
    alpha_aero: float
    alpha_up: float
    alpha_down: float
    k1: float
    k2: float


class MechanicalGreyBox:
    """
    Trip-level mechanical energy model:

        m = rho_batt * bus_battery_kwh + k1 * bus_length_m + k2

        E_mech = alpha_roll  * m * L
               + alpha_aero  * L * v^2 * (driving_time / total_duration)
               + alpha_up    * m * h_up
               + alpha_down  * m * h_down

    where:
        L              = total_distance_m
        v              = driving_average_speed_kmh / 3.6  [m/s]
        h_up           = total_ascent_m
        h_down         = total_descent_m
        driving_time   = driving_time_minutes
        total_duration = total_duration_minutes

    The dwell time correction (driving_time / total_duration) accounts for
    the fact that aerodynamic drag only applies when the bus is moving,
    not during stops/dwell time.
    """

    def __init__(self, battery_pack_density_kg_per_kwh: float = 6.0) -> None:
        self.battery_pack_density = battery_pack_density_kg_per_kwh
        self.params_: Optional[GreyBoxParams] = None

    @staticmethod
    def _extract_arrays(X: pd.DataFrame):
        for col in REQUIRED_COLS:
            if col not in X.columns:
                raise ValueError(f"Missing required column '{col}' in X")

        L = X["total_distance_m"].to_numpy(dtype=float)
        v = X["driving_average_speed_kmh"].to_numpy(dtype=float) / 3.6
        h_up = X["total_ascent_m"].to_numpy(dtype=float)
        h_down = X["total_descent_m"].to_numpy(dtype=float)
        length = X["bus_length_m"].to_numpy(dtype=float)
        batt_kwh = X["bus_battery_kwh"].to_numpy(dtype=float)
        driving_time_minutes = X["driving_time_minutes"].to_numpy(dtype=float)
        total_duration_minutes = X["total_duration_minutes"].to_numpy(dtype=float)
        return L, v, h_up, h_down, length, batt_kwh, driving_time_minutes, total_duration_minutes

    def _predict_with_params(
        self,
        X: pd.DataFrame,
        theta: np.ndarray,
        override_mass: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        alpha_roll, alpha_aero, alpha_up, alpha_down, k1, k2 = theta
        L, v, h_up, h_down, length, batt_kwh, driving_time_minutes, total_duration_minutes = self._extract_arrays(X)
        if override_mass is not None:
            m = np.asarray(override_mass, dtype=float)
        else:
            m = self.battery_pack_density * batt_kwh + k1 * length + k2
        dwell_time_factor = driving_time_minutes / total_duration_minutes
        E_mech = (
            alpha_roll * m * L
            + alpha_aero * L * (v ** 2) * dwell_time_factor
            + alpha_up * m * h_up
            + alpha_down * m * h_down
        )
        return E_mech

    def fit(self, X: pd.DataFrame, y: pd.Series) -> "MechanicalGreyBox":
        # Included for completeness; training happens offline
        from scipy.optimize import least_squares

        X_local = X[REQUIRED_COLS].copy()
        y_vec = y.to_numpy(dtype=float)

        def residuals(theta: np.ndarray) -> np.ndarray:
            pred = self._predict_with_params(X_local, theta)
            return pred - y_vec

        x0 = np.array([1e-6, 1e-9, 1e-6, 1e-6, 1e-3, 0.0], dtype=float)
        res = least_squares(residuals, x0, method="trf")
        prms = GreyBoxParams(*res.x)
        self.params_ = prms
        return self

    def predict(
        self,
        X: pd.DataFrame,
        override_mass: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        if self.params_ is None:
            raise RuntimeError("MechanicalGreyBox not fitted yet.")
        theta = np.array([
            self.params_.alpha_roll,
            self.params_.alpha_aero,
            self.params_.alpha_up,
            self.params_.alpha_down,
            self.params_.k1,
            self.params_.k2,
        ], dtype=float)
        X_local = X[REQUIRED_COLS].copy()
        return self._predict_with_params(X_local, theta, override_mass=override_mass)

    def get_params_dict(self) -> Dict[str, float]:
        if self.params_ is None:
            return {}
        return {
            "alpha_roll": float(self.params_.alpha_roll),
            "alpha_aero": float(self.params_.alpha_aero),
            "alpha_up": float(self.params_.alpha_up),
            "alpha_down": float(self.params_.alpha_down),
            "k1": float(self.params_.k1),
            "k2": float(self.params_.k2),
            "battery_pack_density": float(self.battery_pack_density),
        }


def compute_aux_energy(X_frame: pd.DataFrame, aux_lookup: Optional[Dict[str, Tuple[np.ndarray, np.ndarray]]]) -> pd.Series:
    if aux_lookup is None:
        return pd.Series(0.0, index=X_frame.index, dtype=np.float64)

    required = {"bus_number", "avg_temp_outside_celsius", "total_duration_minutes"}
    if not required.issubset(set(X_frame.columns)):
        return pd.Series(0.0, index=X_frame.index, dtype=np.float64)

    result = pd.Series(index=X_frame.index, dtype=np.float64)
    for bus_number, group in X_frame.groupby("bus_number"):
        key = str(bus_number)
        if aux_lookup is None or key not in aux_lookup:
            result.loc[group.index] = 0.0
            continue
        temps, powers = aux_lookup[key]
        ext = group["avg_temp_outside_celsius"].to_numpy(dtype=np.float64)
        dur = group["total_duration_minutes"].to_numpy(dtype=np.float64)
        power = np.interp(ext, temps, powers)
        energy = power * dur / 60.0
        result.loc[group.index] = energy
    return result


class CombinedGreyboxQRF:
    """
    Wrapper combining a fitted MechanicalGreyBox and a residual QRF.
    Provides predict(X) and predict(X, quantiles=[...]) interfaces.
    """

    def __init__(
        self,
        greybox: MechanicalGreyBox,
        qrf: RandomForestQuantileRegressor,
        selected_features: List[str],
        aux_lookup: Optional[Dict[str, Tuple[np.ndarray, np.ndarray]]] = None,
        include_greybox_feature: bool = True,
        greybox_feature_name: str = GREYBOX_PRED_FEATURE,
        passenger_load_estimator: Optional[dict] = None,
    ) -> None:
        self.greybox = greybox
        self.qrf = qrf
        self.selected_features = selected_features
        self.aux_lookup = aux_lookup
        self.include_greybox_feature = include_greybox_feature
        self.greybox_feature_name = greybox_feature_name
        self.passenger_load_estimator = passenger_load_estimator

    def _aux_energy(self, X: pd.DataFrame) -> np.ndarray:
        ser = compute_aux_energy(X, self.aux_lookup)
        return ser.to_numpy(dtype=float)

    def _fallback_training_mass(self, X: pd.DataFrame) -> Optional[np.ndarray]:
        """Reproduce training mass only when no runtime mass is supplied.

        Production requests always provide ``override_mass`` from the selected
        bus model.  This fallback exists for offline compatibility checks and
        standalone predictions of the serialized training artifact.
        """

        estimator = getattr(self, "passenger_load_estimator", None)
        if not isinstance(estimator, dict):
            return None
        config = estimator.get("config", {})
        mapping = estimator.get("time_bin_load_factor")
        if isinstance(mapping, dict):
            minutes = int(config.get("time_bin_minutes", 60))
            if minutes <= 0 or 1440 % minutes:
                raise ValueError("Serialized passenger time-bin width is invalid")
            if "start_time_minutes" not in X.columns:
                raise ValueError(
                    "start_time_minutes is required by the serialized hourly "
                    "passenger-load fallback"
                )
            starts = X["start_time_minutes"].to_numpy(dtype=float)
            if not np.isfinite(starts).all():
                raise ValueError("start_time_minutes must be finite")
            bins = ((starts % 1440.0) // minutes).astype(int)
            prior = float(config.get("prior_load_factor", 0.5))
            occupancy = np.array(
                [float(mapping.get(str(time_bin), mapping.get(time_bin, prior))) for time_bin in bins],
                dtype=float,
            )
            passenger_weight = float(config.get("passenger_weight_kg", 70.0))
        elif estimator.get("mode") == "constant":
            occupancy = np.full(
                len(X), float(estimator.get("prior_load_factor", 0.5)), dtype=float
            )
            passenger_weight = float(estimator.get("passenger_weight_kg", 70.0))
        else:
            return None

        if ((occupancy < 0) | (occupancy > 1)).any():
            raise ValueError("Serialized passenger load factors must be within [0, 1]")
        length = X["bus_length_m"].to_numpy(dtype=float)
        battery = X["bus_battery_kwh"].to_numpy(dtype=float)
        mass_contract = estimator.get("mass_contract", {})
        battery_density = float(
            mass_contract.get(
                "battery_density_kg_per_kwh", self.greybox.battery_pack_density
            )
        )
        fitted_params = getattr(self.greybox, "params_", None)
        default_chassis_k1 = getattr(self.greybox, "chassis_mass_k1", None)
        default_chassis_k2 = getattr(self.greybox, "chassis_mass_k2", None)
        if default_chassis_k1 is None and fitted_params is not None:
            default_chassis_k1 = fitted_params.k1
        if default_chassis_k2 is None and fitted_params is not None:
            default_chassis_k2 = fitted_params.k2
        chassis_k1 = float(
            mass_contract.get(
                "chassis_k1_kg_per_m", default_chassis_k1
            )
        )
        chassis_k2 = float(
            mass_contract.get(
                "chassis_k2_kg", default_chassis_k2
            )
        )
        capacity = np.interp(
            length,
            SOLARIS_LENGTHS_M,
            SOLARIS_PASSENGER_CAPACITIES,
            left=SOLARIS_PASSENGER_CAPACITIES[0],
            right=SOLARIS_PASSENGER_CAPACITIES[-1],
        )
        mass = (
            chassis_k1 * length
            + chassis_k2
            + battery_density * battery
            + capacity * occupancy * passenger_weight
        )
        if not np.isfinite(mass).all() or (mass <= 0).any():
            raise ValueError("Serialized passenger mass fallback is invalid")
        return mass

    def predict(
        self,
        X: pd.DataFrame,
        quantiles: Optional[List[float]] = None,
        aux_energy_fn: Optional[Callable[[pd.DataFrame], Union[np.ndarray, pd.Series]]] = None,
        override_mass: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        effective_mass = override_mass
        if effective_mass is None:
            effective_mass = self._fallback_training_mass(X)
        gb_pred = self.greybox.predict(X, override_mass=effective_mass)
        if aux_energy_fn is not None:
            aux_out = aux_energy_fn(X)
            if isinstance(aux_out, pd.Series):
                aux = aux_out.to_numpy(dtype=float)
            else:
                aux = np.asarray(aux_out, dtype=float).reshape(-1)
        else:
            aux = self._aux_energy(X)

        X_qrf = X
        if "bus_number" in X_qrf.columns:
            X_qrf = X_qrf.drop(columns=["bus_number"])
        if "bus_battery_kwh" in X_qrf.columns:
            X_qrf = X_qrf.drop(columns=["bus_battery_kwh"])
        selected_features = self.selected_features or []
        requires_gb_pred_feature = GREYBOX_PRED_FEATURE in selected_features

        if requires_gb_pred_feature:
            X_qrf = X_qrf.copy()
            X_qrf[GREYBOX_PRED_FEATURE] = gb_pred

        if selected_features:
            X_qrf = X_qrf[selected_features]

        if quantiles is None:
            res_pred = self.qrf.predict(X_qrf)
            total = gb_pred + res_pred + aux
            return total
        else:
            # Support special "mean" directive from quantile_forest
            if isinstance(quantiles, str) and quantiles == "mean":
                res_mean = self.qrf.predict(X_qrf, quantiles="mean")
                total_mean = gb_pred + res_mean + aux
                return total_mean
            res_q = self.qrf.predict(X_qrf, quantiles=quantiles)
            total_q = res_q + gb_pred[:, None] + aux[:, None]
            return total_q
