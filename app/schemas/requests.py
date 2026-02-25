from __future__ import annotations

from uuid import UUID
from pydantic import BaseModel, Field, model_validator
from typing import Literal, Optional
from app.schemas.trip_status import TripStatus


class PredictionRequest(BaseModel):
    shift_ids: list[UUID]
    bus_model_id: UUID
    model_name: str = Field(examples=["greybox_qrf_production_crps_optimized_3"])
    external_temp_celsius: float = Field(examples=[15.0])
    occupancy_percent: float = Field(default=50.0, examples=[50.0])
    auxiliary_heating_type: str = Field(default="default", examples=["default"])
    quantiles: list[float] = Field(default_factory=lambda: [0.05, 0.25, 0.5, 0.75, 0.95])
    num_battery_packs: Optional[int] = Field(default=None, examples=[12])


# ---------------------------------------------------------------------------
# Optimization request schemas
# ---------------------------------------------------------------------------

class ChargingStationConfig(BaseModel):
    """Configuration for a candidate (or fixed) charging station at a stop."""
    stop_id: UUID
    slot_costs_chf: Optional[list[float]] = Field(
        default=None,
        examples=[[350000, 150000, 150000]],
        description="Marginal installation cost per additional slot (charging_only / joint)",
    )
    num_slots: Optional[int] = Field(
        default=None, examples=[2],
        description="Fixed number of installed slots (battery_only mode)",
    )
    max_total_power_kw: float = Field(examples=[450.0])
    max_power_per_slot_kw: Optional[float] = Field(
        default=None, examples=[450.0],
        description="Per-slot power cap; defaults to bus max_charging_power_kw",
    )


class PredictionParams(BaseModel):
    """Parameters for auto-prediction when prediction_run_ids is not given."""
    model_name: str = Field(default="greybox_qrf_production_crps_optimized_3")
    external_temp_celsius: float = Field(examples=[15.0])
    occupancy_percent: float = Field(default=50.0, examples=[50.0])
    auxiliary_heating_type: str = Field(default="default", examples=["default"])
    quantiles: list[float] = Field(default_factory=lambda: [0.05, 0.5, 0.95])
    num_battery_packs: Optional[int] = Field(default=None, examples=[12])


class OptimizationRequest(BaseModel):
    mode: Literal["battery_only", "charging_only", "joint"]
    shift_ids: list[UUID]
    bus_model_id: Optional[UUID] = Field(
        default=None,
        description="Bus model for all shifts. If omitted, each shift's bus model is resolved from the DB (shift->bus->bus_model).",
    )

    prediction_run_ids: Optional[list[UUID]] = Field(
        default=None,
        description="Reuse existing prediction runs; if omitted, predictions are auto-created",
    )
    prediction_params: Optional[PredictionParams] = Field(
        default=None,
        description="Parameters for auto-prediction (required when prediction_run_ids is not given)",
    )

    charging_stations: list[ChargingStationConfig] = Field(
        default_factory=list,
        description="Charging station candidates (or fixed stations for battery_only)",
    )

    # SOC limits
    min_soc: float = Field(default=0.4, ge=0.0, le=1.0, examples=[0.4])
    max_soc: float = Field(default=0.9, ge=0.0, le=1.0, examples=[0.9])
    state_of_health: float = Field(default=1.0, ge=0.0, le=1.0, examples=[1.0])
    quantile_consumption: str = Field(
        default="mean", examples=["mean"],
        description="Which prediction quantile to use: mean, median, or a numeric quantile like 0.95",
    )

    # Battery sizing
    battery_sizing_mode: Literal["per_bus", "per_route"] = Field(
        default="per_bus",
        description="Only for battery_only / joint: per_bus allows different sizes, per_route ties buses on same route",
    )
    battery_cost_per_kwh: Optional[float] = Field(
        default=None, examples=[300.0],
        description="CHF/kWh for battery capacity within [min_packs, max_packs] (joint mode only)",
    )
    max_battery_penalty_per_kwh: float = Field(
        default=1e6, examples=[1000000.0],
        description="CHF/kWh penalty for exceeding max_battery_packs (soft infeasibility)",
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

