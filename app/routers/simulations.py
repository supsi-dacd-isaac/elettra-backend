"""
Simulation endpoints for battery optimization
"""

import asyncio
from datetime import datetime
from typing import Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from uuid import UUID

from app.database import get_db, User, SimulationRun, BusModel
from app.schemas import SimulationInput, SimulationResponse
from app.core.auth import verify_jwt_token

router = APIRouter()

async def battery_optimization_algorithm(
    input_params: Dict[str, Any],
    bus_specs: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Battery optimization algorithm for electric bus simulations
    
    This algorithm processes flexible input parameters and returns
    comprehensive optimization results.
    """
    
    # Simulate processing time
    await asyncio.sleep(2)
    
    # Extract parameters with defaults
    route_length_km = input_params.get("route_length_km", 20.0)
    daily_trips = input_params.get("daily_trips", 30)
    passenger_load_factor = input_params.get("passenger_load_factor", 0.6)
    terrain_factor = input_params.get("terrain_factor", 1.0)
    climate_factor = input_params.get("climate_factor", 1.0)
    safety_margin = input_params.get("safety_margin", 0.2)
    charging_strategy = input_params.get("charging_strategy", "overnight")
    average_speed_kmh = input_params.get("average_speed_kmh", 25.0)
    elevation_gain_m = input_params.get("elevation_gain_m", 0)
    stop_frequency = input_params.get("stop_frequency", 10)
    hvac_usage = input_params.get("hvac_usage", "moderate")
    
    # Get bus specifications with defaults
    bus_capacity = bus_specs.get("passenger_capacity", 80)
    bus_weight_kg = bus_specs.get("weight_kg", 12000)
    bus_length_m = bus_specs.get("length_m", 12)
    
    # Algorithm calculations
    
    # 1. Base energy consumption (kWh/km)
    base_consumption = 1.2
    
    # 2. Apply various factors
    load_factor = 1.0 + (passenger_load_factor * 0.3)
    weight_factor = bus_weight_kg / 12000
    speed_factor = 1.0 + abs(average_speed_kmh - 25) * 0.01
    elevation_factor = 1.0 + (elevation_gain_m / 1000) * 0.1
    stop_factor = 1.0 + (stop_frequency / 20) * 0.15
    
    # HVAC factor
    hvac_factors = {"low": 1.05, "moderate": 1.15, "high": 1.25}
    hvac_factor = hvac_factors.get(hvac_usage, 1.15)
    
    # Charging strategy efficiency
    charging_efficiencies = {
        "overnight": 0.92,
        "opportunity": 0.88,
        "fast": 0.85,
        "ultra_fast": 0.82
    }
    charging_efficiency = charging_efficiencies.get(charging_strategy, 0.90)
    
    # Total energy consumption per km
    energy_per_km = (
        base_consumption * 
        load_factor * 
        weight_factor * 
        speed_factor * 
        terrain_factor * 
        climate_factor * 
        elevation_factor * 
        stop_factor * 
        hvac_factor
    )
    
    # Daily energy requirements
    daily_energy_kwh = energy_per_km * route_length_km * daily_trips
    
    # Battery sizing
    degradation_factor = 0.8
    usable_capacity_factor = 0.9  # Don't use 100% of battery capacity
    
    required_capacity = daily_energy_kwh / (degradation_factor * usable_capacity_factor * charging_efficiency)
    optimal_battery_kwh = required_capacity * (1 + safety_margin)
    
    # Cost calculations (Swiss market)
    battery_cost_per_kwh = 450  # CHF
    installation_cost = 25000  # CHF
    total_battery_cost = (optimal_battery_kwh * battery_cost_per_kwh) + installation_cost
    
    # Infrastructure costs based on charging strategy
    infrastructure_costs = {
        "overnight": 15000,    # Depot charging
        "opportunity": 45000,  # Route charging stations
        "fast": 75000,         # Fast charging infrastructure
        "ultra_fast": 120000   # Ultra-fast charging network
    }
    infrastructure_cost = infrastructure_costs.get(charging_strategy, 30000)
    
    # Environmental calculations
    diesel_consumption_l_per_km = 0.35
    co2_per_liter_diesel = 2.64  # kg CO2
    swiss_electricity_co2_factor = 0.109  # kg CO2/kWh (very low due to hydro/nuclear)
    
    annual_diesel_co2 = (
        route_length_km * daily_trips * 365 * 
        diesel_consumption_l_per_km * co2_per_liter_diesel
    )
    annual_electric_co2 = daily_energy_kwh * 365 * swiss_electricity_co2_factor
    annual_co2_reduction = annual_diesel_co2 - annual_electric_co2
    
    # Operational metrics
    max_charging_power = {
        "overnight": 22,      # kW
        "opportunity": 150,   # kW
        "fast": 350,         # kW
        "ultra_fast": 450    # kW
    }
    charging_power = max_charging_power.get(charging_strategy, 50)
    charging_time_hours = optimal_battery_kwh / charging_power
    
    # Range calculations
    max_range_km = (optimal_battery_kwh * usable_capacity_factor) / energy_per_km
    range_safety_factor = max_range_km / (route_length_km * daily_trips)
    
    return {
        "algorithm_results": {
            "optimal_battery_capacity_kwh": round(optimal_battery_kwh, 1),
            "daily_energy_consumption_kwh": round(daily_energy_kwh, 2),
            "energy_consumption_per_km": round(energy_per_km, 3),
            "max_range_km": round(max_range_km, 1),
            "range_safety_factor": round(range_safety_factor, 2)
        },
        "cost_analysis": {
            "battery_cost_chf": round(total_battery_cost, 0),
            "infrastructure_cost_chf": infrastructure_cost,
            "total_investment_chf": round(total_battery_cost + infrastructure_cost, 0),
            "annual_energy_cost_chf": round(daily_energy_kwh * 365 * 0.22, 2),
            "annual_fuel_savings_chf": round(
                route_length_km * daily_trips * 365 * 
                diesel_consumption_l_per_km * 1.65, 2
            )
        },
        "environmental_impact": {
            "annual_co2_reduction_kg": round(annual_co2_reduction, 1),
            "annual_co2_reduction_percentage": round(
                (annual_co2_reduction / annual_diesel_co2) * 100, 1
            ) if annual_diesel_co2 > 0 else 0,
            "equivalent_trees_planted": round(annual_co2_reduction / 22, 0)
        },
        "technical_specifications": {
            "charging_strategy": charging_strategy,
            "charging_power_kw": charging_power,
            "charging_time_hours": round(charging_time_hours, 2),
            "charging_efficiency": charging_efficiency,
            "battery_weight_kg": round(optimal_battery_kwh * 6.5, 1)
        },
        "operational_factors": {
            "load_factor": round(load_factor, 3),
            "weight_factor": round(weight_factor, 3),
            "speed_factor": round(speed_factor, 3),
            "terrain_factor": terrain_factor,
            "climate_factor": climate_factor,
            "elevation_factor": round(elevation_factor, 3),
            "stop_factor": round(stop_factor, 3),
            "hvac_factor": hvac_factor
        },
        "input_parameters": input_params,
        "bus_specifications": bus_specs,
        "calculation_metadata": {
            "algorithm_version": "2.0.0",
            "timestamp": datetime.utcnow().isoformat(),
            "processing_time_seconds": 2.0
        }
    }

async def run_simulation_background(
    simulation_id: UUID,
    input_params: Dict[str, Any],
    bus_specs: Dict[str, Any]
):
    """Background task to run the simulation"""
    from app.database import AsyncSessionLocal
    
    async with AsyncSessionLocal() as db:
        try:
            # Update status to running
            await db.execute(
                update(SimulationRun)
                .where(SimulationRun.id == simulation_id)
                .values(status="running")
            )
            await db.commit()
            
            # Run the algorithm
            results = await battery_optimization_algorithm(input_params, bus_specs)
            optimal_battery = results["algorithm_results"]["optimal_battery_capacity_kwh"]
            
            # Update with results
            await db.execute(
                update(SimulationRun)
                .where(SimulationRun.id == simulation_id)
                .values(
                    status="completed",
                    optimal_battery_kwh=optimal_battery,
                    output_results=results,
                    completed_at=datetime.utcnow()
                )
            )
            await db.commit()
            
        except Exception as e:
            # Update status to failed
            await db.execute(
                update(SimulationRun)
                .where(SimulationRun.id == simulation_id)
                .values(
                    status="failed",
                    output_results={"error": str(e), "timestamp": datetime.utcnow().isoformat()},
                    completed_at=datetime.utcnow()
                )
            )
            await db.commit()

@router.post("/run", response_model=SimulationResponse)
async def run_simulation(
    simulation_input: SimulationInput,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(verify_jwt_token),
    db: AsyncSession = Depends(get_db)
):
    """
    Run battery optimization simulation with flexible parameters
    
    This endpoint accepts a flexible JSON structure for simulation parameters,
    allowing for easy extension without API changes.
    
    **Example Usage:**
    ```json
    {
        "bus_model_id": "550e8400-e29b-41d4-a716-446655440000",
        "input_params": {
            "route_length_km": 25.5,
            "daily_trips": 45,
            "passenger_load_factor": 0.7,
            "terrain_factor": 1.2,
            "climate_factor": 1.1,
            "charging_strategy": "overnight",
            "safety_margin": 0.25,
            "average_speed_kmh": 30.0,
            "elevation_gain_m": 150,
            "stop_frequency": 12,
            "hvac_usage": "moderate"
        }
    }
    ```
    
    **Supported Parameters in input_params:**
    - `route_length_km` (float): Route length in kilometers
    - `daily_trips` (int): Number of daily trips
    - `passenger_load_factor` (float, 0-1): Average passenger load
    - `terrain_factor` (float, 0.8-1.5): Terrain difficulty
    - `climate_factor` (float, 0.8-1.4): Climate impact
    - `charging_strategy` (str): "overnight", "opportunity", "fast", "ultra_fast"
    - `safety_margin` (float, 0.1-0.5): Battery safety margin
    - `average_speed_kmh` (float): Average operating speed
    - `elevation_gain_m` (float): Total elevation gain
    - `stop_frequency` (int): Number of stops per route
    - `hvac_usage` (str): "low", "moderate", "high"
    """
    
    # Verify bus model exists and belongs to user's company
    bus_model_result = await db.execute(
        select(BusModel).where(
            BusModel.id == simulation_input.bus_model_id,
            BusModel.company_id == current_user.company_id
        )
    )
    bus_model = bus_model_result.scalar_one_or_none()
    
    if not bus_model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bus model not found or does not belong to your company"
        )
    
    # Create simulation run record
    simulation = SimulationRun(
        company_id=current_user.company_id,
        user_id=current_user.id,
        bus_model_id=simulation_input.bus_model_id,
        input_params=simulation_input.input_params,  # Store the flexible dict
        status="pending"
    )
    
    db.add(simulation)
    await db.commit()
    await db.refresh(simulation)
    
    # Start background simulation
    background_tasks.add_task(
        run_simulation_background,
        simulation.id,
        simulation_input.input_params,
        bus_model.specs
    )
    
    # Return initial simulation record
    response = SimulationResponse.from_orm(simulation)
    
    # Add bus model details
    from app.schemas import BusModelResponse
    response.bus_model = BusModelResponse.from_orm(bus_model)
    
    return response

@router.get("/{simulation_id}/status")
async def get_simulation_status(
    simulation_id: UUID,
    current_user: User = Depends(verify_jwt_token),
    db: AsyncSession = Depends(get_db)
):
    """Get the current status of a simulation"""
    
    result = await db.execute(
        select(SimulationRun).where(
            SimulationRun.id == simulation_id,
            SimulationRun.company_id == current_user.company_id
        )
    )
    simulation = result.scalar_one_or_none()
    
    if not simulation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Simulation not found"
        )
    
    return {
        "simulation_id": simulation.id,
        "status": simulation.status,
        "created_at": simulation.created_at,
        "completed_at": simulation.completed_at,
        "has_results": simulation.output_results is not None,
        "input_params": simulation.input_params
    }

@router.get("/parameters/template")
async def get_parameter_template():
    """
    Get a template of supported simulation parameters
    
    This endpoint returns a comprehensive template showing all supported
    parameters that can be included in the input_params dictionary.
    """
    return {
        "template": {
            "route_length_km": {
                "type": "float",
                "description": "Route length in kilometers",
                "example": 25.5,
                "required": True
            },
            "daily_trips": {
                "type": "integer", 
                "description": "Number of daily trips",
                "example": 45,
                "required": True
            },
            "passenger_load_factor": {
                "type": "float",
                "description": "Average passenger load factor (0.0 to 1.0)",
                "example": 0.7,
                "default": 0.6
            },
            "terrain_factor": {
                "type": "float",
                "description": "Terrain difficulty factor (0.8 to 1.5)",
                "example": 1.2,
                "default": 1.0
            },
            "climate_factor": {
                "type": "float",
                "description": "Climate impact factor (0.8 to 1.4)",
                "example": 1.1,
                "default": 1.0
            },
            "charging_strategy": {
                "type": "string",
                "description": "Charging strategy type",
                "example": "overnight",
                "options": ["overnight", "opportunity", "fast", "ultra_fast"],
                "default": "overnight"
            },
            "safety_margin": {
                "type": "float",
                "description": "Battery safety margin (0.1 to 0.5)",
                "example": 0.25,
                "default": 0.2
            },
            "average_speed_kmh": {
                "type": "float",
                "description": "Average operating speed in km/h",
                "example": 30.0,
                "default": 25.0
            },
            "elevation_gain_m": {
                "type": "float",
                "description": "Total elevation gain in meters",
                "example": 150,
                "default": 0
            },
            "stop_frequency": {
                "type": "integer",
                "description": "Number of stops per route",
                "example": 12,
                "default": 10
            },
            "hvac_usage": {
                "type": "string",
                "description": "HVAC usage level",
                "example": "moderate",
                "options": ["low", "moderate", "high"],
                "default": "moderate"
            }
        },
        "example_request": {
            "bus_model_id": "550e8400-e29b-41d4-a716-446655440000",
            "input_params": {
                "route_length_km": 25.5,
                "daily_trips": 45,
                "passenger_load_factor": 0.7,
                "terrain_factor": 1.2,
                "climate_factor": 1.1,
                "charging_strategy": "overnight",
                "safety_margin": 0.25,
                "average_speed_kmh": 30.0,
                "elevation_gain_m": 150,
                "stop_frequency": 12,
                "hvac_usage": "moderate"
            }
        }
    } 