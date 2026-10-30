from __future__ import annotations

import math

from typing import Annotated, Any, Literal, Optional
from uuid import UUID
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictFloat,
    StrictInt,
    field_validator,
    model_validator,
)
from app.schemas.trip_status import TripStatus
from app.services.runtime_release import (
    RuntimeReleaseConfigurationError,
    default_prediction_model_name,
    default_prediction_stack_name,
    resolve_prediction_selection,
)

# Centralised allowed values for auxiliary heating mode.
# "default" = standard ebus auxiliary (heat-pump based, fully electric)
# "diesel"  = diesel-heating ebus-dh (heating shifted to diesel, cooling stays electric)
AuxiliaryHeatingType = Literal["default", "diesel"]
PredictionStackType = Literal["legacy", "vecto-g2", "vecto-g0-transfer"]


PositiveFiniteFloat = Annotated[
    StrictFloat,
    Field(gt=0, allow_inf_nan=False),
]
PositiveStrictInt = Annotated[StrictInt, Field(ge=1)]


def _trim_non_empty_bus_model_name(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("name must be a non-empty string")
    trimmed = value.strip()
    if not trimmed:
        raise ValueError("name must not be empty or whitespace-only")
    return trimmed


def _remove_omitted_field_default(schema: dict[str, Any]) -> None:
    """Do not advertise the internal omission sentinel as a JSON default."""

    schema.pop("default", None)


class BusModelPhysicalSpecs(BaseModel):
    """Physical fields required when bus-model specs are written.

    Read responses deliberately keep the historical permissive JSON contract:
    old database rows may still be incomplete.  This model is therefore used
    only by create/update requests.  Additional economic, LCA, charging and
    auxiliary-consumption fields remain valid and are preserved verbatim.
    """

    model_config = ConfigDict(extra="allow")

    bus_length_m: PositiveFiniteFloat = Field(
        description="Physical bus length in metres.",
    )
    empty_weight_kg: PositiveFiniteFloat = Field(
        description=(
            "Empty vehicle mass in kilograms, excluding the traction battery "
            "and passengers."
        ),
    )
    battery_pack_size_kwh: PositiveFiniteFloat = Field(
        description="Installed capacity of one battery pack in kWh, before the SOC operating window and SOH are applied.",
    )
    battery_pack_weight_kg: PositiveFiniteFloat = Field(
        description="Mass of one battery pack in kilograms.",
    )
    min_battery_packs: PositiveStrictInt
    max_battery_packs: PositiveStrictInt
    max_passengers: PositiveStrictInt

    @model_validator(mode="after")
    def validate_battery_pack_bounds(self):
        if self.min_battery_packs > self.max_battery_packs:
            raise ValueError(
                "min_battery_packs must be less than or equal to "
                "max_battery_packs"
            )
        return self


class BusModelCreateRequest(BaseModel):
    """Create a bus model with a complete physical specification."""

    name: str
    specs: BusModelPhysicalSpecs
    user_id: UUID
    manufacturer: Optional[str] = None
    description: Optional[str] = None

    @field_validator("name", mode="before")
    @classmethod
    def trim_and_validate_name(cls, value: Any) -> str:
        return _trim_non_empty_bus_model_name(value)


class BusModelUpdateRequest(BaseModel):
    """Update bus-model metadata or replace its complete specs object.

    Omitting ``specs`` permits metadata-only edits to historical incomplete
    records.  Explicit ``null`` is rejected; when supplied, the entire specs
    object must satisfy :class:`BusModelPhysicalSpecs`.
    """

    id: Optional[UUID] = None
    name: Optional[str] = None
    # A non-nullable type with an omitted default keeps PATCH-like omission
    # semantics while making explicit JSON null invalid (and documenting the
    # field as non-nullable in OpenAPI).
    specs: BusModelPhysicalSpecs = Field(  # type: ignore[assignment]
        default=None,
        json_schema_extra=_remove_omitted_field_default,
    )
    user_id: Optional[UUID] = None
    manufacturer: Optional[str] = None
    description: Optional[str] = None

    @field_validator("name", mode="before")
    @classmethod
    def trim_and_validate_name(cls, value: Any) -> str:
        return _trim_non_empty_bus_model_name(value)


def _resolve_prediction_stack_fields(model: BaseModel) -> BaseModel:
    """Resolve optional API selectors to one registered model/stack pair."""

    explicit = model.model_fields_set
    requested_stack = (
        getattr(model, "prediction_stack") if "prediction_stack" in explicit else None
    )
    requested_model = getattr(model, "model_name") if "model_name" in explicit else None
    try:
        release = resolve_prediction_selection(
            prediction_stack=requested_stack,
            model_name=requested_model,
        )
    except RuntimeReleaseConfigurationError as exc:
        raise ValueError(str(exc)) from exc
    model.prediction_stack = release.stack.value
    model.model_name = release.model_release
    return model


def _validated_quantiles(values: list[float]) -> list[float]:
    normalized = [float(value) for value in values]
    if (
        not normalized
        or len(normalized) != len(set(normalized))
        or len(normalized) != len({f"{value:.2f}" for value in normalized})
        or not all(0 < value < 1 for value in normalized)
    ):
        raise ValueError(
            "quantiles must be a non-empty unique list with values in (0, 1)"
        )
    return normalized


class PredictionRequest(BaseModel):
    shift_ids: list[UUID]
    bus_model_id: UUID
    yearly_analysis_id: Optional[UUID] = Field(
        default=None,
        description="Optional reference to a yearly analysis record.",
    )
    model_name: str = Field(
        default_factory=default_prediction_model_name,
        examples=["greybox_qrf_production_crps_optimized_3"],
    )
    prediction_stack: PredictionStackType = Field(
        default_factory=default_prediction_stack_name,
        description=(
            "Registered prediction stack. Omit it to use the server default. "
            "When model_name is also supplied, both selectors must resolve to "
            "the same configured release."
        ),
    )
    external_temp_celsius: float = Field(examples=[15.0])
    occupancy_percent: float = Field(
        default=50.0,
        ge=0.0,
        le=100.0,
        allow_inf_nan=False,
        examples=[50.0],
    )
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

    @field_validator("quantiles")
    @classmethod
    def validate_quantiles(cls, values: list[float]) -> list[float]:
        return _validated_quantiles(values)

    @model_validator(mode="after")
    def resolve_prediction_stack(self):
        return _resolve_prediction_stack_fields(self)


# ---------------------------------------------------------------------------
# Optimization request schemas
# ---------------------------------------------------------------------------

class InfrastructureCostConfig(BaseModel):
    """One-time CAPEX. Power-priced infrastructure uses the configured AC connection."""
    station_type: Literal["depot", "pantograph"]
    point_cost_chf: float = Field(ge=0, allow_inf_nan=False)
    site_fixed_cost_chf: float = Field(ge=0, allow_inf_nan=False)
    connection_cost_per_kw_chf: float = Field(ge=0, allow_inf_nan=False)
    ac_dc_efficiency: float = Field(default=0.95, gt=0, le=1, allow_inf_nan=False)
    source: str = Field(default="infras2026-calibrated-v1", max_length=100)


class ChargingStationConfig(BaseModel):
    """Configuration for a candidate (or fixed) charging station at a stop.

    Which fields are required depends on the optimization mode:

    - With `cost_model`, `num_slots` is the fixed count in **battery_only** and
      the maximum candidate count in **charging_only** / **joint**.
    - Legacy requests use `num_slots` in **battery_only**, or marginal
      `slot_costs_chf` in **charging_only** / **joint**.
    - **All modes**: `stop_id`, `max_total_power_kw` are always required.
      `max_power_per_slot_kw` is optional in every mode (defaults to the bus-level power cap).
    """
    stop_id: UUID
    cost_model: Optional[InfrastructureCostConfig] = None
    slot_costs_chf: Optional[list[float]] = Field(
        default=None,
        examples=[[350000, 150000, 150000]],
        description=(
            "Marginal installation cost (CHF) per additional charger slot. "
            "Legacy alternative to cost_model for charging_only and joint. Ignored in battery_only."
        ),
    )
    num_slots: Optional[int] = Field(
        default=None, examples=[2],
        description=(
            "Fixed charger count in battery_only; maximum candidate count when "
            "using cost_model in charging_only or joint."
        ),
    )
    max_total_power_kw: float = Field(
        examples=[450.0],
        description="Maximum aggregate DC output (kW) at this station across all active chargers; not an AC grid-connection limit. Required in all modes.",
    )
    max_power_per_slot_kw: Optional[float] = Field(
        default=None, examples=[450.0],
        description=(
            "Maximum DC power (kW) a single charger slot can deliver to the bus. "
            "When omitted, only the bus-level and station-total power limits apply. "
            "Applicable in all modes."
        ),
    )


class PredictionParams(BaseModel):
    """Parameters for auto-prediction when prediction_run_ids is not given."""
    model_name: str = Field(default_factory=default_prediction_model_name)
    prediction_stack: PredictionStackType = Field(
        default_factory=default_prediction_stack_name
    )
    external_temp_celsius: float = Field(examples=[15.0])
    occupancy_percent: float = Field(
        default=50.0,
        ge=0.0,
        le=100.0,
        allow_inf_nan=False,
        examples=[50.0],
    )
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

    @field_validator("quantiles")
    @classmethod
    def validate_quantiles(cls, values: list[float]) -> list[float]:
        return _validated_quantiles(values)

    @model_validator(mode="after")
    def resolve_prediction_stack(self):
        return _resolve_prediction_stack_fields(self)


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
      must specify `cost_model` with `num_slots` (or legacy `slot_costs_chf`)
      and `max_total_power_kw` (and optionally
      `max_power_per_slot_kw`).
    - **joint**: Both charger installation and battery sizing are optimized
      simultaneously. Requires `cost_model` with `num_slots` (or legacy
      `slot_costs_chf`) per station, `max_total_power_kw`,
      and the battery pack cost in the bus model. `max_power_per_slot_kw` is
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
    reference_prediction_run_ids: Optional[dict[UUID, UUID]] = Field(
        default=None,
        description="One reference prediction per shift; prediction_run_ids remains the comparison catalogue.",
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
            "are candidates (cost_model with num_slots, or legacy slot_costs_chf)."
        ),
    )

    # SOC limits
    min_soc: float = Field(default=0.4, ge=0.0, le=1.0, examples=[0.4])
    max_soc: float = Field(default=0.9, ge=0.0, le=1.0, examples=[0.9])
    state_of_health: float = Field(default=1.0, ge=0.0, le=1.0, examples=[1.0])
    quantile_consumption: str = Field(
        default="mean", examples=["mean"],
        description="Legacy per-trip basis: mean, median, or a numeric quantile. 0.95 is NOT aggregate Q95.",
    )
    consumption_basis: Optional[Literal["mean", "aggregate_q95"]] = Field(
        default=None,
        description="Project decision basis. aggregate_q95 samples independent trip occurrences and conditions the profile on the shift total Q95. None preserves the legacy per-trip setting.",
    )
    evaluate_both_bases: bool = Field(default=True,
        description="Calculate linked Mean and aggregate-Q95 designs by default. Explicit single-basis requests and legacy per-trip/session inputs remain supported.")
    aggregate_sample_count: int = Field(default=100_000, ge=10_000, le=1_000_000)
    aggregate_max_samples: int = Field(default=500_000, ge=10_000, le=1_000_000)
    aggregate_seed: int = Field(default=20261029, ge=0, le=4294967295)
    aggregate_precision_kwh: float = Field(default=0.25, gt=0, le=10)

    # Battery sizing
    battery_sizing_mode: Literal["per_bus", "uniform", "per_route"] = Field(
        default="per_bus",
        description=(
            "battery_only / joint only. 'per_bus' allows each bus to have a different "
            "battery size; 'uniform' selects one pack count for all buses (same pack size). "
            "'per_route' is retained only for legacy requests."
        ),
    )
    battery_cost_per_kwh: Optional[float] = Field(
        default=None, examples=[300.0], ge=0, allow_inf_nan=False,
        description="Legacy fallback only. Joint optimization uses battery_pack_cost_chf from each bus model.",
    )
    max_battery_penalty_per_kwh: float = Field(
        default=1e6, examples=[1000000.0],
        description=(
            "CHF/kWh penalty for exceeding max_battery_packs (soft infeasibility slack). "
            "Used in charging_only (excess-pack penalty) and joint modes."
        ),
    )

    # Depot charging
    daily_cycle: bool = Field(default=False, description="Verify a repeating 24-hour duty and recharge at the existing start/end depot before the next departure.")
    depot_peak_shaving: bool = Field(default=False, description="Simulate depot smart charging with hardware, plug assignments and daytime charging fixed.")
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
    optimization_time_budget_seconds: int = Field(default=900, ge=1, le=3600)
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
        if self.depot_peak_shaving and not self.daily_cycle:
            raise ValueError("Depot peak shaving requires a daily cycle")
        if self.daily_cycle and (not self.lock_entire_dwell or self.depot_dwell_minutes_after or self.cp_slack_minutes):
            raise ValueError("Daily cycles require entire-dwell occupancy, no extra depot dwell and zero charger cooldown")
        # Older frontend bundles omit this field. A normal feasibility request
        # must still produce both designs; only historical inputs keep the old
        # default when their quantile/session contract cannot support a pair.
        if "evaluate_both_bases" not in self.model_fields_set:
            self.evaluate_both_bases = self.quantile_consumption == "mean" and self.lock_entire_dwell
        if self.evaluate_both_bases and (self.quantile_consumption != "mean" or not self.lock_entire_dwell):
            raise ValueError("Paired evaluations require mean trip forecasts and entire-dwell occupancy")
        if self.consumption_basis is not None and self.quantile_consumption != "mean":
            raise ValueError("consumption_basis cannot be combined with a legacy per-trip quantile")
        if self.aggregate_max_samples < self.aggregate_sample_count:
            raise ValueError("aggregate_max_samples must be at least aggregate_sample_count")
        if self.min_soc >= self.max_soc or self.state_of_health <= 0:
            raise ValueError("A positive usable SOC window and state_of_health are required")
        if not self.shift_ids or len(set(self.shift_ids)) != len(self.shift_ids):
            raise ValueError("shift_ids must be non-empty and unique")
        if self.reference_prediction_run_ids:
            if set(self.reference_prediction_run_ids) != set(self.shift_ids):
                raise ValueError("reference_prediction_run_ids must cover exactly the selected shifts")
            if not set(self.reference_prediction_run_ids.values()) <= set(self.prediction_run_ids or []):
                raise ValueError("References must belong to prediction_run_ids")
        if self.mode == "charging_only" and not self.prediction_run_ids:
            if self.prediction_params is None or self.prediction_params.num_battery_packs is None:
                raise ValueError("charging_only requires an explicit battery pack count")
        if self.mode in ("charging_only", "joint"):
            for cs in self.charging_stations:
                if not cs.slot_costs_chf and cs.cost_model is None:
                    raise ValueError(
                        f"slot_costs_chf is required for each charging station in {self.mode} mode"
                    )
        for cs in self.charging_stations:
            if cs.cost_model is not None:
                if cs.num_slots is None or cs.num_slots < 0:
                    raise ValueError("Costed stations require a nonnegative num_slots")
                if cs.slot_costs_chf is not None:
                    raise ValueError("Use cost_model or legacy slot_costs_chf, not both")
                if not math.isfinite(cs.max_total_power_kw) or cs.max_total_power_kw < 0:
                    raise ValueError("Costed stations require finite nonnegative DC site power")
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
