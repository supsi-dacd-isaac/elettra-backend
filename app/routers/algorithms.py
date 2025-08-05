"""
Energy consumption and battery sizing algorithms
Enhanced with real Swiss route data from pfaedle processing
"""

import math
from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.schemas import (
    EnergyConsumptionInput, EnergyConsumptionResult,
    BatterySizingInput, BatterySizingResult, BusType
)
from app.core.auth import verify_jwt_token
from app.database import get_db, User, TransitRoute, RouteShape, RouteStop

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

@router.post("/energy-consumption/route/{route_id}", response_model=EnergyConsumptionResult)
async def calculate_energy_consumption_for_route(
    route_id: str,
    bus_type: BusType,
    passenger_capacity: int,
    climate_factor: float = 1.1,
    current_user: User = Depends(verify_jwt_token),
    db: AsyncSession = Depends(get_db)
):
    """
    Calculate energy consumption for a specific Swiss transit route
    
    This endpoint uses real route data processed by pfaedle to provide
    more accurate energy consumption estimates based on:
    - Actual route distance and elevation profile
    - Real stop patterns and frequencies
    - Swiss-specific terrain factors
    
    **Parameters:**
    - `route_id`: ID of the transit route from the database
    - `bus_type`: Type of electric bus to simulate
    - `passenger_capacity`: Maximum passenger capacity
    - `climate_factor`: Climate adjustment (1.1 for Swiss winter conditions)
    """
    
    # Get route with shapes and stops
    result = await db.execute(
        select(TransitRoute)
        .options(
            selectinload(TransitRoute.route_shapes),
            selectinload(TransitRoute.route_stops)
        )
        .where(
            TransitRoute.id == route_id,
            TransitRoute.company_id == current_user.company_id
        )
    )
    
    route = result.scalar_one_or_none()
    
    if not route:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Route not found"
        )
    
    # Calculate route characteristics from real data
    route_analysis = await _analyze_route_characteristics(route)
    
    # Enhanced energy calculation with real route data
    bus_coeff = BUS_TYPE_COEFFICIENTS[bus_type]
    base_consumption = bus_coeff["base_consumption"] * 100  # kWh/100km
    
    # Route-specific factors
    distance_km = route_analysis["total_distance_km"]
    elevation_factor = 1.0 + (route_analysis["elevation_gain_per_km"] / 50)  # 50m/km baseline
    stop_frequency_factor = 1.0 + (route_analysis["stops_per_km"] * 0.05)  # 5% per stop/km
    terrain_factor = min(elevation_factor * 1.2, 2.0)  # Swiss terrain adjustment
    
    # Speed estimation based on stop frequency
    estimated_speed = max(20, 45 - (route_analysis["stops_per_km"] * 5))
    speed_factor = 1.0 + (abs(estimated_speed - 35) / 100)
    
    # Passenger load (Swiss public transport average)
    occupancy_factor = 0.65  # Higher than general assumption for Swiss public transport
    passenger_load = passenger_capacity * occupancy_factor
    capacity_factor = 1.0 + (passenger_load / 100)
    
    # Calculate energy consumption
    energy_per_100km = (
        base_consumption * 
        bus_coeff["weight_factor"] * 
        speed_factor * 
        capacity_factor * 
        terrain_factor * 
        climate_factor
    )
    
    # Daily energy based on typical Swiss bus operations (8-12 round trips per day)
    daily_trips = 10
    daily_energy = (energy_per_100km / 100) * distance_km * daily_trips
    monthly_energy = daily_energy * 22
    annual_energy = daily_energy * 250
    
    return EnergyConsumptionResult(
        energy_consumption_kwh_per_100km=round(energy_per_100km, 2),
        daily_energy_requirement_kwh=round(daily_energy, 2),
        monthly_energy_requirement_kwh=round(monthly_energy, 2),
        annual_energy_requirement_kwh=round(annual_energy, 2),
        calculation_parameters={
            "route_name": route.route_short_name or route.route_long_name,
            "actual_distance_km": round(distance_km, 2),
            "elevation_gain_per_km": round(route_analysis["elevation_gain_per_km"], 1),
            "stops_per_km": round(route_analysis["stops_per_km"], 1),
            "estimated_speed_kmh": round(estimated_speed, 1),
            "terrain_factor": round(terrain_factor, 3),
            "speed_factor": round(speed_factor, 3),
            "capacity_factor": round(capacity_factor, 3),
            "climate_factor": climate_factor,
            "bus_type": bus_type.value,
            "daily_trips": daily_trips,
            "passenger_load": round(passenger_load, 1)
        }
    )

async def _analyze_route_characteristics(route: TransitRoute) -> Dict[str, float]:
    """Analyze route characteristics from real data"""
    
    analysis = {
        "total_distance_km": 0.0,
        "elevation_gain_per_km": 0.0,
        "stops_per_km": 0.0,
        "avg_grade_percent": 0.0
    }
    
    # Get total distance from route shapes
    if route.route_shapes:
        total_distance_m = sum([
            shape.total_distance_m or 0 
            for shape in route.route_shapes
        ])
        analysis["total_distance_km"] = total_distance_m / 1000
        
        # Average elevation data
        elevation_gains = [
            shape.elevation_gain_m or 0 
            for shape in route.route_shapes 
            if shape.elevation_gain_m
        ]
        if elevation_gains and analysis["total_distance_km"] > 0:
            total_elevation = sum(elevation_gains)
            analysis["elevation_gain_per_km"] = total_elevation / analysis["total_distance_km"]
    
    # Calculate stop frequency
    if route.route_stops and analysis["total_distance_km"] > 0:
        analysis["stops_per_km"] = len(route.route_stops) / analysis["total_distance_km"]
    
    # Default values if no real data available
    if analysis["total_distance_km"] == 0:
        analysis["total_distance_km"] = 15.0  # Default urban route length
        analysis["elevation_gain_per_km"] = 25.0  # Moderate Swiss terrain
        analysis["stops_per_km"] = 2.0  # Typical urban bus stop frequency
    
    return analysis

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

@router.get("/route-analysis/{route_id}")
async def analyze_route_for_electrification(
    route_id: str,
    current_user: User = Depends(verify_jwt_token),
    db: AsyncSession = Depends(get_db)
):
    """
    Analyze a specific route for electric bus conversion feasibility
    
    Provides detailed analysis including:
    - Route characteristics (distance, elevation, stops)
    - Energy requirements for different bus types
    - Infrastructure requirements
    - Cost estimates
    """
    
    # Get route data
    result = await db.execute(
        select(TransitRoute)
        .options(
            selectinload(TransitRoute.route_shapes),
            selectinload(TransitRoute.route_stops)
        )
        .where(
            TransitRoute.id == route_id,
            TransitRoute.company_id == current_user.company_id
        )
    )
    
    route = result.scalar_one_or_none()
    
    if not route:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Route not found"
        )
    
    # Analyze route characteristics
    route_analysis = await _analyze_route_characteristics(route)
    
    # Calculate energy requirements for all bus types
    energy_analysis = {}
    for bus_type in BusType:
        bus_coeff = BUS_TYPE_COEFFICIENTS[bus_type]
        
        # Simplified calculation for comparison
        base_consumption = bus_coeff["base_consumption"] * 100
        terrain_factor = 1.0 + (route_analysis["elevation_gain_per_km"] / 50)
        daily_energy = (base_consumption / 100) * route_analysis["total_distance_km"] * 10  # 10 trips
        
        energy_analysis[bus_type.value] = {
            "daily_energy_kwh": round(daily_energy, 1),
            "recommended_battery_kwh": round(daily_energy * 1.3, 1),  # 30% margin
            "estimated_cost_chf": round(daily_energy * 1.3 * 450, 0)
        }
    
    return {
        "route_info": {
            "id": route.id,
            "name": route.route_short_name or route.route_long_name,
            "agency": route.agency_name,
            "route_type": route.route_type
        },
        "route_characteristics": route_analysis,
        "electrification_analysis": energy_analysis,
        "recommendations": {
            "feasibility": "High" if route_analysis["total_distance_km"] < 25 else "Medium",
            "recommended_bus_type": "standard" if route_analysis["total_distance_km"] < 20 else "articulated",
            "charging_strategy": "opportunity" if route_analysis["stops_per_km"] > 1.5 else "depot",
            "infrastructure_priority": "High" if route_analysis["elevation_gain_per_km"] < 30 else "Medium"
        }
    }

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
                "energy_efficiency": "High",
                "best_for": "Low-density routes, feeder services"
            },
            {
                "type": "standard",
                "description": "Standard city bus (up to 90 passengers)",
                "typical_capacity": "70-90",
                "energy_efficiency": "Good",
                "best_for": "Urban routes, medium capacity"
            },
            {
                "type": "articulated",
                "description": "Articulated bus (up to 150 passengers)",
                "typical_capacity": "120-150",
                "energy_efficiency": "Moderate",
                "best_for": "High-capacity urban routes, BRT"
            },
            {
                "type": "double_decker",
                "description": "Double-decker bus (up to 120 passengers)",
                "typical_capacity": "100-120",
                "energy_efficiency": "Good",
                "best_for": "Tourist routes, limited headroom constraints"
            }
        ]
    } 