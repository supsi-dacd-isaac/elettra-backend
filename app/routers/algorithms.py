"""
Energy consumption and battery sizing algorithms
"""

import math
from typing import Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from app.schemas import (
    EnergyConsumptionInput, EnergyConsumptionResult,
    BatterySizingInput, BatterySizingResult, BusType
)
from app.core.auth import verify_jwt_token
from app.database import User

router = APIRouter()

# Algorithm constants for Swiss electric buses
BUS_TYPE_COEFFICIENTS = {
    BusType.MINI: {"base_consumption": 0.8, "weight_factor": 0.7},
    BusType.STANDARD: {"base_consumption": 1.2, "weight_factor": 1.0},
    BusType.ARTICULATED: {"base_consumption": 1.8, "weight_factor": 1.4},
    BusType.DOUBLE_DECKER: {"base_consumption": 1.6, "weight_factor": 1.3}
}

@router.post("/energy-consumption", response_model=EnergyConsumptionResult)
async def calculate_energy_consumption(
    input_data: EnergyConsumptionInput,
    current_user: User = Depends(verify_jwt_token)
):
    """
    Calculate energy consumption for electric bus operations
    
    This algorithm estimates energy consumption based on:
    - Bus type and capacity
    - Route characteristics
    - Operating conditions
    - Swiss terrain and climate factors
    
    **Example Usage:**
    ```json
    {
        "route_length_km": 25.5,
        "bus_type": "standard",
        "passenger_capacity": 80,
        "average_speed_kmh": 30.0,
        "terrain_factor": 1.2,
        "climate_factor": 1.1
    }
    ```
    """
    try:
        # Get bus type coefficients
        bus_coeff = BUS_TYPE_COEFFICIENTS[input_data.bus_type]
        
        # Base energy consumption (kWh/100km)
        base_consumption = bus_coeff["base_consumption"] * 100  # kWh/100km
        
        # Passenger load factor (assuming 60% average occupancy)
        occupancy_factor = 0.6
        passenger_load = input_data.passenger_capacity * occupancy_factor
        
        # Energy consumption factors
        speed_factor = 1.0 + (abs(input_data.average_speed_kmh - 35) / 100)  # Optimal at 35 km/h
        capacity_factor = 1.0 + (passenger_load / 100)  # Additional energy per passenger
        
        # Calculate energy consumption per 100km
        energy_per_100km = (
            base_consumption * 
            bus_coeff["weight_factor"] * 
            speed_factor * 
            capacity_factor * 
            input_data.terrain_factor * 
            input_data.climate_factor
        )
        
        # Daily energy requirements
        daily_energy = (energy_per_100km / 100) * input_data.route_length_km
        monthly_energy = daily_energy * 22  # Average working days per month
        annual_energy = daily_energy * 250  # Average working days per year
        
        return EnergyConsumptionResult(
            energy_consumption_kwh_per_100km=round(energy_per_100km, 2),
            daily_energy_requirement_kwh=round(daily_energy, 2),
            monthly_energy_requirement_kwh=round(monthly_energy, 2),
            annual_energy_requirement_kwh=round(annual_energy, 2),
            calculation_parameters={
                "base_consumption_kwh_100km": base_consumption,
                "speed_factor": round(speed_factor, 3),
                "capacity_factor": round(capacity_factor, 3),
                "terrain_factor": input_data.terrain_factor,
                "climate_factor": input_data.climate_factor,
                "bus_type": input_data.bus_type.value,
                "passenger_load": round(passenger_load, 1)
            }
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Energy calculation failed: {str(e)}"
        )

@router.post("/battery-sizing", response_model=BatterySizingResult)
async def calculate_battery_sizing(
    input_data: BatterySizingInput,
    current_user: User = Depends(verify_jwt_token)
):
    """
    Calculate optimal battery sizing for electric bus
    
    This algorithm determines:
    - Recommended battery capacity
    - Cost estimates (Swiss market)
    - Charging requirements
    - Weight implications
    
    **Example Usage:**
    ```json
    {
        "daily_energy_requirement_kwh": 250.0,
        "safety_margin": 0.25,
        "degradation_factor": 0.85,
        "charging_efficiency": 0.92
    }
    ```
    """
    try:
        # Minimum battery capacity (considering degradation and efficiency)
        min_capacity = (
            input_data.daily_energy_requirement_kwh / 
            (input_data.degradation_factor * input_data.charging_efficiency)
        )
        
        # Recommended capacity (with safety margin)
        recommended_capacity = min_capacity * (1 + input_data.safety_margin)
        
        # Swiss market battery costs (CHF per kWh) - 2024 estimates
        cost_per_kwh_chf = 450  # Including installation and infrastructure
        battery_cost = recommended_capacity * cost_per_kwh_chf
        
        # Charging time estimation (assuming 150kW fast charging)
        charging_power_kw = 150
        charging_time = recommended_capacity / charging_power_kw
        
        # Battery weight estimation (kg per kWh)
        weight_per_kwh = 6.5  # Modern Li-ion batteries
        battery_weight = recommended_capacity * weight_per_kwh
        
        return BatterySizingResult(
            recommended_battery_capacity_kwh=round(recommended_capacity, 1),
            minimum_battery_capacity_kwh=round(min_capacity, 1),
            battery_cost_estimate_chf=round(battery_cost, 0),
            charging_time_hours=round(charging_time, 2),
            battery_weight_kg=round(battery_weight, 1)
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Battery sizing calculation failed: {str(e)}"
        )

@router.get("/bus-types")
async def get_bus_types():
    """
    Get available bus types and their characteristics
    """
    return {
        "bus_types": [
            {
                "type": "mini",
                "description": "Mini bus (up to 25 passengers)",
                "typical_capacity": "15-25",
                "energy_efficiency": "High"
            },
            {
                "type": "standard",
                "description": "Standard city bus (up to 90 passengers)",
                "typical_capacity": "70-90",
                "energy_efficiency": "Good"
            },
            {
                "type": "articulated",
                "description": "Articulated bus (up to 150 passengers)",
                "typical_capacity": "120-150",
                "energy_efficiency": "Moderate"
            },
            {
                "type": "double_decker",
                "description": "Double-decker bus (up to 120 passengers)",
                "typical_capacity": "100-120",
                "energy_efficiency": "Good"
            }
        ]
    } 