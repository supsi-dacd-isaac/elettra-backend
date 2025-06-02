"""
Complete electrification analysis endpoints
"""

from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.database import get_db, User, ElectrificationProject
from app.schemas import (
    ElectrificationProjectCreate, ElectrificationProjectResponse,
    FeasibilityAnalysis, EnergyConsumptionInput, BatterySizingInput
)
from app.core.auth import verify_jwt_token
from app.routers.algorithms import calculate_energy_consumption, calculate_battery_sizing

router = APIRouter()

@router.post("/projects", response_model=ElectrificationProjectResponse)
async def create_project(
    project_data: ElectrificationProjectCreate,
    current_user: User = Depends(verify_jwt_token),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new electrification analysis project
    
    **Example Usage:**
    ```json
    {
        "name": "Route 42 Electrification",
        "description": "Analysis for electrifying bus route 42 in Zurich",
        "route_length_km": 18.5,
        "bus_type": "standard",
        "passenger_capacity": 85,
        "daily_trips": 45
    }
    ```
    """
    # Create project
    db_project = ElectrificationProject(
        user_id=current_user.id,
        company_id=current_user.company_id,
        **project_data.dict()
    )
    
    db.add(db_project)
    await db.commit()
    await db.refresh(db_project)
    
    return ElectrificationProjectResponse.from_orm(db_project)

@router.get("/projects", response_model=List[ElectrificationProjectResponse])
async def get_projects(
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(verify_jwt_token),
    db: AsyncSession = Depends(get_db)
):
    """
    Get list of electrification projects for current user's company
    """
    result = await db.execute(
        select(ElectrificationProject)
        .where(ElectrificationProject.company_id == current_user.company_id)
        .offset(skip)
        .limit(limit)
        .order_by(ElectrificationProject.created_at.desc())
    )
    projects = result.scalars().all()
    return [ElectrificationProjectResponse.from_orm(project) for project in projects]

@router.get("/projects/{project_id}", response_model=ElectrificationProjectResponse)
async def get_project(
    project_id: UUID,
    current_user: User = Depends(verify_jwt_token),
    db: AsyncSession = Depends(get_db)
):
    """
    Get specific electrification project
    """
    result = await db.execute(
        select(ElectrificationProject).where(
            ElectrificationProject.id == project_id,
            ElectrificationProject.company_id == current_user.company_id
        )
    )
    project = result.scalar_one_or_none()
    
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found"
        )
    
    return ElectrificationProjectResponse.from_orm(project)

@router.post("/projects/{project_id}/analyze", response_model=FeasibilityAnalysis)
async def analyze_project_feasibility(
    project_id: UUID,
    terrain_factor: float = 1.0,
    climate_factor: float = 1.0,
    current_user: User = Depends(verify_jwt_token),
    db: AsyncSession = Depends(get_db)
):
    """
    Perform complete feasibility analysis for electrification project
    
    This endpoint runs both energy consumption and battery sizing algorithms
    and provides comprehensive analysis including economic and environmental factors.
    
    **Parameters:**
    - **terrain_factor**: Terrain difficulty factor (0.8-1.5, default 1.0)
    - **climate_factor**: Climate impact factor (0.8-1.4, default 1.0)
    """
    # Get project
    result = await db.execute(
        select(ElectrificationProject).where(
            ElectrificationProject.id == project_id,
            ElectrificationProject.company_id == current_user.company_id
        )
    )
    project = result.scalar_one_or_none()
    
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found"
        )
    
    try:
        # Energy consumption analysis
        energy_input = EnergyConsumptionInput(
            route_length_km=project.route_length_km,
            bus_type=project.bus_type,
            passenger_capacity=project.passenger_capacity,
            terrain_factor=terrain_factor,
            climate_factor=climate_factor
        )
        
        energy_result = await calculate_energy_consumption(energy_input, current_user)
        
        # Battery sizing analysis
        battery_input = BatterySizingInput(
            daily_energy_requirement_kwh=energy_result.daily_energy_requirement_kwh
        )
        
        battery_result = await calculate_battery_sizing(battery_input, current_user)
        
        # Economic analysis (simplified)
        diesel_cost_per_liter = 1.65  # CHF (average Swiss price)
        diesel_consumption_per_100km = 35  # liters
        electricity_cost_per_kwh = 0.22  # CHF (Swiss average)
        
        annual_diesel_cost = (
            (project.route_length_km / 100) * 
            diesel_consumption_per_100km * 
            diesel_cost_per_liter * 
            project.daily_trips * 
            250
        )
        
        annual_electricity_cost = (
            energy_result.annual_energy_requirement_kwh * 
            electricity_cost_per_kwh
        )
        
        annual_savings = annual_diesel_cost - annual_electricity_cost
        payback_years = battery_result.battery_cost_estimate_chf / annual_savings if annual_savings > 0 else float('inf')
        
        economic_analysis = {
            "annual_diesel_cost_chf": round(annual_diesel_cost, 2),
            "annual_electricity_cost_chf": round(annual_electricity_cost, 2),
            "annual_savings_chf": round(annual_savings, 2),
            "battery_investment_chf": battery_result.battery_cost_estimate_chf,
            "payback_period_years": round(payback_years, 1) if payback_years != float('inf') else None
        }
        
        # Environmental analysis
        co2_per_liter_diesel = 2.64  # kg CO2
        annual_co2_diesel = (
            (project.route_length_km / 100) * 
            diesel_consumption_per_100km * 
            co2_per_liter_diesel * 
            project.daily_trips * 
            250
        )
        
        # Swiss electricity CO2 factor (kg CO2/kWh) - very low due to hydro/nuclear
        co2_per_kwh_swiss = 0.109
        annual_co2_electric = energy_result.annual_energy_requirement_kwh * co2_per_kwh_swiss
        
        co2_reduction = annual_co2_diesel - annual_co2_electric
        
        environmental_analysis = {
            "annual_co2_diesel_kg": round(annual_co2_diesel, 2),
            "annual_co2_electric_kg": round(annual_co2_electric, 2),
            "annual_co2_reduction_kg": round(co2_reduction, 2),
            "co2_reduction_percentage": round((co2_reduction / annual_co2_diesel) * 100, 1) if annual_co2_diesel > 0 else 0
        }
        
        # Calculate feasibility score (0-100)
        score_factors = {
            "economic": min(100, max(0, (annual_savings / 50000) * 40)),  # Up to 40 points
            "environmental": min(30, (co2_reduction / 10000) * 30),  # Up to 30 points
            "technical": 20 if battery_result.battery_weight_kg < 3000 else 10,  # Technical feasibility
            "route_suitability": min(10, (project.route_length_km / 50) * 10)  # Route length factor
        }
        
        feasibility_score = sum(score_factors.values())
        
        # Generate recommendations
        recommendations = []
        
        if feasibility_score >= 80:
            recommendations.append("Highly recommended for electrification")
        elif feasibility_score >= 60:
            recommendations.append("Good candidate for electrification with proper planning")
        elif feasibility_score >= 40:
            recommendations.append("Consider electrification with infrastructure improvements")
        else:
            recommendations.append("Electrification may not be economically viable at this time")
        
        if payback_years and payback_years < 5:
            recommendations.append("Excellent economic return on investment")
        elif payback_years and payback_years < 10:
            recommendations.append("Good long-term investment")
        
        if co2_reduction > 5000:
            recommendations.append("Significant environmental benefits")
        
        if battery_result.charging_time_hours < 4:
            recommendations.append("Fast charging compatible")
        
        # Update project with results
        await db.execute(
            select(ElectrificationProject).where(ElectrificationProject.id == project_id).update({
                "estimated_energy_consumption": energy_result.energy_consumption_kwh_per_100km,
                "recommended_battery_size": battery_result.recommended_battery_capacity_kwh,
                "feasibility_score": feasibility_score
            })
        )
        await db.commit()
        
        return FeasibilityAnalysis(
            energy_analysis=energy_result,
            battery_analysis=battery_result,
            economic_analysis=economic_analysis,
            environmental_analysis=environmental_analysis,
            feasibility_score=round(feasibility_score, 1),
            recommendations=recommendations
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Analysis failed: {str(e)}"
        )

@router.delete("/projects/{project_id}")
async def delete_project(
    project_id: UUID,
    current_user: User = Depends(verify_jwt_token),
    db: AsyncSession = Depends(get_db)
):
    """
    Delete electrification project
    """
    result = await db.execute(
        delete(ElectrificationProject).where(
            ElectrificationProject.id == project_id,
            ElectrificationProject.company_id == current_user.company_id
        )
    )
    
    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found"
        )
    
    await db.commit()
    return {"message": "Project deleted successfully"} 