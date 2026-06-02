from __future__ import annotations

from uuid import UUID
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Literal, Optional
from app.schemas.trip_status import TripStatus

# Centralised allowed values for auxiliary heating mode.
# "default" = standard ebus auxiliary (heat-pump based, fully electric)
# "diesel"  = diesel-heating ebus-dh (heating shifted to diesel, cooling stays electric)
AuxiliaryHeatingType = Literal["default", "diesel"]


class PredictionRequest(BaseModel):
    shift_ids: list[UUID]
    bus_model_id: UUID
    yearly_analysis_id: Optional[UUID] = Field(
        default=None,
        description="Optional reference to a yearly analysis record.",
    )
    model_name: str = Field(examples=["greybox_qrf_production_crps_optimized_3"])
    external_temp_celsius: float = Field(examples=[15.0])
    occupancy_percent: float = Field(default=50.0, examples=[50.0])
    auxiliary_heating_type: AuxiliaryHeatingType = Field(
        default="default",
        description=(
            "Auxiliary heating mode. 'default' keeps full-electric auxiliary "
            "(heat pump). 'diesel' shifts heating to a diesel heater while "
            "cooling remains electric."
        ),
        examples=["default", "diesel"],
    )
    quantiles: list[float] = Field(default_factory=lambda: [0.05, 0.25, 0.5, 0.75, 0.95])
    num_battery_packs: Optional[int] = Field(default=None, examples=[12])


# ---------------------------------------------------------------------------
# Optimization request schemas
# ---------------------------------------------------------------------------

class ChargingStationConfig(BaseModel):
    """Configuration for a candidate (or fixed) charging station at a stop.

    Which fields are required depends on the optimization mode:

    - **battery_only**: `num_slots` is required (chargers are fixed, battery is optimized).
      `slot_costs_chf` is ignored.
    - **charging_only** / **joint**: `slot_costs_chf` is required (charger installation
      is optimized). `num_slots` is ignored.
    - **All modes**: `stop_id`, `max_total_power_kw` are always required.
      `max_power_per_slot_kw` is optional in every mode (defaults to the bus-level power cap).
    """
    stop_id: UUID
    slot_costs_chf: Optional[list[float]] = Field(
        default=None,
        examples=[[350000, 150000, 150000]],
        description=(
            "Marginal installation cost (CHF) per additional charger slot. "
            "Required for charging_only and joint modes. Ignored in battery_only."
        ),
    )
    num_slots: Optional[int] = Field(
        default=None, examples=[2],
        description=(
            "Number of already-installed charger slots at this station. "
            "Required for battery_only mode. Ignored in charging_only and joint."
        ),
    )
    max_total_power_kw: float = Field(
        examples=[450.0],
        description="Maximum aggregate power (kW) at this station across all active chargers. Required in all modes.",
    )
    max_power_per_slot_kw: Optional[float] = Field(
        default=None, examples=[450.0],
        description=(
            "Maximum power (kW) a single charger slot can deliver. "
            "When omitted, only the bus-level and station-total power limits apply. "
            "Applicable in all modes."
        ),
    )


class PredictionParams(BaseModel):
    """Parameters for auto-prediction when prediction_run_ids is not given."""
    model_name: str = Field(default="greybox_qrf_production_crps_optimized_3")
    external_temp_celsius: float = Field(examples=[15.0])
    occupancy_percent: float = Field(default=50.0, examples=[50.0])
    auxiliary_heating_type: AuxiliaryHeatingType = Field(
        default="default",
        description=(
            "Auxiliary heating mode. 'default' keeps full-electric auxiliary "
            "(heat pump). 'diesel' shifts heating to a diesel heater while "
            "cooling remains electric."
        ),
        examples=["default", "diesel"],
    )
    quantiles: list[float] = Field(default_factory=lambda: [0.05, 0.5, 0.95])
    num_battery_packs: Optional[int] = Field(default=None, examples=[12])


_EXAMPLE_SHIFT_ID = "00000000-0000-0000-0000-000000000001"
_EXAMPLE_PRED_ID = "00000000-0000-0000-0000-000000000010"
_EXAMPLE_STOP_ID = "00000000-0000-0000-0000-000000000100"

_OPTIMIZATION_EXAMPLES = {
    "charging_only": {
        "summary": "charging_only -- optimize charger installation, battery fixed",
        "value": {
            "name": "Charging-only feasibility evaluation",
            "mode": "charging_only",
            "auxiliary_heating_type": "default",
            "shift_ids": [_EXAMPLE_SHIFT_ID],
            "prediction_run_ids": [_EXAMPLE_PRED_ID],
            "charging_stations": [
                {
                    "stop_id": _EXAMPLE_STOP_ID,
                    "slot_costs_chf": [350000, 150000],
                    "max_total_power_kw": 450,
                    "max_power_per_slot_kw": 450,
                }
            ],
            "min_soc": 0.4,
            "max_soc": 0.9,
            "solver_name": "highs",
            "max_solver_time_seconds": 300,
        },
    },
    "battery_only_diesel": {
        "summary": "battery_only with diesel heating -- heating shifted to diesel",
        "value": {
            "name": "Battery-only diesel-heating evaluation",
            "mode": "battery_only",
            "auxiliary_heating_type": "diesel",
            "shift_ids": [_EXAMPLE_SHIFT_ID],
            "prediction_params": {
                "external_temp_celsius": -5.0,
                "auxiliary_heating_type": "diesel",
            },
            "charging_stations": [
                {
                    "stop_id": _EXAMPLE_STOP_ID,
                    "num_slots": 2,
                    "max_total_power_kw": 450,
                    "max_power_per_slot_kw": 150,
                }
            ],
            "min_soc": 0.4,
            "max_soc": 0.9,
            "solver_name": "highs",
            "max_solver_time_seconds": 300,
        },
    },
    "battery_only": {
        "summary": "battery_only -- optimize battery sizing, chargers fixed",
        "value": {
            "name": "Battery-only feasibility evaluation",
            "mode": "battery_only",
            "shift_ids": [_EXAMPLE_SHIFT_ID],
            "prediction_run_ids": [_EXAMPLE_PRED_ID],
            "charging_stations": [
                {
                    "stop_id": _EXAMPLE_STOP_ID,
                    "num_slots": 2,
                    "max_total_power_kw": 450,
                    "max_power_per_slot_kw": 150,
                }
            ],
            "min_soc": 0.4,
            "max_soc": 0.9,
            "solver_name": "highs",
            "max_solver_time_seconds": 300,
        },
    },
    "joint": {
        "summary": "joint -- optimize both chargers and battery sizing",
        "value": {
            "name": "Joint feasibility evaluation",
            "mode": "joint",
            "shift_ids": [_EXAMPLE_SHIFT_ID],
            "prediction_run_ids": [_EXAMPLE_PRED_ID],
            "charging_stations": [
                {
                    "stop_id": _EXAMPLE_STOP_ID,
                    "slot_costs_chf": [350000, 150000],
                    "max_total_power_kw": 450,
                    "max_power_per_slot_kw": 450,
                }
            ],
            "battery_cost_per_kwh": 300.0,
            "battery_sizing_mode": "per_route",
            "min_soc": 0.4,
            "max_soc": 0.9,
            "solver_name": "highs",
            "max_solver_time_seconds": 300,
        },
    },
}


class OptimizationRequest(BaseModel):
    """Fleet charging and battery optimization request.

    Three optimization modes are supported:

    - **battery_only**: Charger locations are fixed; the solver optimizes battery
      pack count per bus. Each station in `charging_stations` must specify `num_slots`
      and `max_total_power_kw` (and optionally `max_power_per_slot_kw`).
    - **charging_only**: Battery sizes are fixed (from the prediction reference);
      the solver decides where to install chargers and how many slots. Each station
      must specify `slot_costs_chf` and `max_total_power_kw` (and optionally
      `max_power_per_slot_kw`).
    - **joint**: Both charger installation and battery sizing are optimized
      simultaneously. Requires `slot_costs_chf` per station, `max_total_power_kw`,
      and `battery_cost_per_kwh` at the request level. `max_power_per_slot_kw` is
      optional.

    Bus model specs (pack size, min/max packs, max charging power) are resolved
    from the explicit simulation bus_model_id first, then from linked prediction
    runs, and only then from shift -> bus -> bus_model.
    """

    model_config = {"json_schema_extra": {"examples": list(_OPTIMIZATION_EXAMPLES.values())}}

    name: str = Field(
        ...,
        min_length=1,
        examples=["My feasibility evaluation"],
        description=(
            "Human-readable display name for this optimization run. "
            "Required. Leading/trailing whitespace is trimmed; empty or "
            "whitespace-only values are rejected. Stored in "
            "`optimization_runs.name` and not duplicated into `input_params`."
        ),
    )
    mode: Literal["battery_only", "charging_only", "joint"]
    auxiliary_heating_type: AuxiliaryHeatingType = Field(
        default="default",
        description=(
            "Auxiliary heating mode for this optimization. 'default' keeps "
            "full-electric auxiliary (heat pump). 'diesel' shifts heating to "
            "a diesel heater while cooling remains electric. When "
            "prediction_params is used without an explicit heating type, "
            "this top-level value is propagated."
        ),
        examples=["default", "diesel"],
    )
    shift_ids: list[UUID]
    bus_model_id: Optional[UUID] = Field(
        default=None,
        description=(
            "Bus model for the simulation. Takes precedence over shift assignments "
            "and must match linked prediction_run_ids when they are provided. If "
            "omitted, linked prediction runs define each bus's simulation model; "
            "otherwise the shift's bus model is resolved from the DB."
        ),
    )

    prediction_run_ids: Optional[list[UUID]] = Field(
        default=None,
        description=(
            "Reuse existing prediction runs. Their bus_model_id values define "
            "simulation bus specs unless bus_model_id is provided, in which case "
            "they must match it. If omitted, predictions are auto-created."
        ),
    )
    prediction_params: Optional[PredictionParams] = Field(
        default=None,
        description="Parameters for auto-prediction (required when prediction_run_ids is not given).",
    )

    charging_stations: list[ChargingStationConfig] = Field(
        default_factory=list,
        description=(
            "Charging station configurations. In battery_only mode these are fixed "
            "installations (num_slots required). In charging_only / joint mode these "
            "are candidates for the optimizer (slot_costs_chf required)."
        ),
    )

    # SOC limits
    min_soc: float = Field(default=0.4, ge=0.0, le=1.0, examples=[0.4])
    max_soc: float = Field(default=0.9, ge=0.0, le=1.0, examples=[0.9])
    state_of_health: float = Field(default=1.0, ge=0.0, le=1.0, examples=[1.0])
    quantile_consumption: str = Field(
        default="mean", examples=["mean"],
        description="Which prediction quantile to use: mean, median, or a numeric quantile like 0.95.",
    )

    # Battery sizing
    battery_sizing_mode: Literal["per_bus", "per_route"] = Field(
        default="per_bus",
        description=(
            "battery_only / joint only. 'per_bus' allows each bus to have a different "
            "battery size; 'per_route' ties all buses on the same route to the same pack count."
        ),
    )
    battery_cost_per_kwh: Optional[float] = Field(
        default=None, examples=[300.0],
        description="Required for joint mode. CHF per kWh of additional battery capacity within [min_packs, max_packs].",
    )
    max_battery_penalty_per_kwh: float = Field(
        default=1e6, examples=[1000000.0],
        description=(
            "CHF/kWh penalty for exceeding max_battery_packs (soft infeasibility slack). "
            "Used in charging_only (excess-pack penalty) and joint modes."
        ),
    )

    # Depot charging
    depot_dwell_minutes_after: int = Field(
        default=0, ge=0,
        description="Minutes of dwell at last trip's end station after arrival (for depot overnight charging)",
    )

    # Session / timing constraints
    min_session_duration_minutes: int = Field(default=0, ge=0)
    session_connection_minutes: int = Field(default=0, ge=0)
    lock_entire_dwell: bool = Field(default=True)
    cp_slack_minutes: int = Field(default=0, ge=0)

    # Penalty weights
    session_penalty_weight: float = Field(default=0.01, ge=0.0)
    early_charging_weight: float = Field(default=0.0, ge=0.0)

    # Solver parameters
    solver_name: str = Field(default="highs", examples=["highs"])
    max_solver_time_seconds: Optional[int] = Field(default=None, examples=[300])
    mip_rel_gap: Optional[float] = Field(default=None, examples=[0.01])
    mip_abs_gap: Optional[float] = Field(default=None, examples=[0.0])
    feasibility_tol: Optional[float] = Field(default=None, examples=[1e-6])
    optimality_tol: Optional[float] = Field(default=None, examples=[1e-6])

    @field_validator("name", mode="before")
    @classmethod
    def _trim_and_validate_name(cls, v):
        if not isinstance(v, str):
            raise ValueError("name must be a non-empty string")
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("name must not be empty or whitespace-only")
        return trimmed

    @model_validator(mode="after")
    def validate_mode_fields(self):
        if self.mode == "joint" and self.battery_cost_per_kwh is None:
            raise ValueError("battery_cost_per_kwh is required for joint mode")
        if self.mode in ("charging_only", "joint"):
            for cs in self.charging_stations:
                if not cs.slot_costs_chf:
                    raise ValueError(
                        f"slot_costs_chf is required for each charging station in {self.mode} mode"
                    )
        if self.mode == "battery_only":
            for cs in self.charging_stations:
                if cs.num_slots is None:
                    raise ValueError(
                        "num_slots is required for each charging station in battery_only mode"
                    )
        if not self.prediction_run_ids and not self.prediction_params:
            raise ValueError(
                "Either prediction_run_ids or prediction_params must be provided"
            )
        if self.prediction_params is not None:
            if self.prediction_params.auxiliary_heating_type == "default":
                self.prediction_params.auxiliary_heating_type = self.auxiliary_heating_type
        return self


class AuxTripCreate(BaseModel):
    departure_stop_id: UUID
    arrival_stop_id: UUID
    departure_time: str
    arrival_time: str
    route_id: UUID
    status: TripStatus = TripStatus.DEPOT
    calendar_service_key: Optional[str] = None
    day_of_week: Optional[str] = None  # monday..sunday; when set, overrides calendar_service_key


# Shift creation/update requests
class ShiftCreateRequest(BaseModel):
    name: str
    bus_id: Optional[UUID] = None
    trip_ids: list[UUID]


class ShiftUpdateRequest(BaseModel):
    name: Optional[str] = None
    bus_id: Optional[UUID] = None
    trip_ids: Optional[list[UUID]] = None


class TripStatisticsRequest(BaseModel):
    trip_ids: list[UUID]

