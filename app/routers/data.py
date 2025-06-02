"""
Data management endpoints for company-specific data
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, update
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError
from uuid import UUID

from app.database import get_db, User, BusModel, FleetInventory, SimulationRun, Company
from app.schemas import (
    BusModelResponse, BusModelCreate, 
    FleetInventoryResponse, FleetInventoryCreate, FleetInventoryUpdate,
    SimulationResponse, CompanyResponse, SimulationSummary
)
from app.core.auth import verify_jwt_token

router = APIRouter()

# ================================
# COMPANY ENDPOINTS
# ================================

@router.get("/company", response_model=CompanyResponse)
async def get_company_info(
    current_user: User = Depends(verify_jwt_token),
    db: AsyncSession = Depends(get_db)
):
    """Get current user's company information"""
    result = await db.execute(
        select(Company).where(Company.id == current_user.company_id)
    )
    company = result.scalar_one_or_none()
    
    if not company:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found"
        )
    
    return CompanyResponse.from_orm(company)

# ================================
# BUS MODEL ENDPOINTS
# ================================

@router.get("/bus-models", response_model=List[BusModelResponse])
async def get_bus_models(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    current_user: User = Depends(verify_jwt_token),
    db: AsyncSession = Depends(get_db)
):
    """
    Get bus models for current user's company
    
    - **skip**: Number of records to skip
    - **limit**: Maximum number of records to return
    """
    result = await db.execute(
        select(BusModel)
        .where(BusModel.company_id == current_user.company_id)
        .offset(skip)
        .limit(limit)
        .order_by(BusModel.name)
    )
    bus_models = result.scalars().all()
    return [BusModelResponse.from_orm(model) for model in bus_models]

@router.post("/bus-models", response_model=BusModelResponse)
async def create_bus_model(
    bus_model_data: BusModelCreate,
    current_user: User = Depends(verify_jwt_token),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new bus model for the company
    
    **Example Usage:**
    ```json
    {
        "name": "Mercedes Citaro Electric",
        "manufacturer": "Mercedes-Benz",
        "specs": {
            "passenger_capacity": 85,
            "length_m": 12.0,
            "width_m": 2.55,
            "height_m": 3.2,
            "weight_kg": 12500,
            "max_speed_kmh": 80,
            "fuel_type": "electric",
            "doors": 3,
            "accessibility": true,
            "air_conditioning": true,
            "battery_capacity_kwh": 250,
            "range_km": 200
        }
    }
    ```
    """
    try:
        # Create new bus model
        db_bus_model = BusModel(
            company_id=current_user.company_id,
            name=bus_model_data.name,
            manufacturer=bus_model_data.manufacturer,
            specs=bus_model_data.specs
        )
        
        db.add(db_bus_model)
        await db.commit()
        await db.refresh(db_bus_model)
        
        return BusModelResponse.from_orm(db_bus_model)
        
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bus model with name '{bus_model_data.name}' already exists for your company"
        )

@router.get("/bus-models/{bus_model_id}", response_model=BusModelResponse)
async def get_bus_model(
    bus_model_id: UUID,
    current_user: User = Depends(verify_jwt_token),
    db: AsyncSession = Depends(get_db)
):
    """Get specific bus model by ID"""
    result = await db.execute(
        select(BusModel).where(
            BusModel.id == bus_model_id,
            BusModel.company_id == current_user.company_id
        )
    )
    bus_model = result.scalar_one_or_none()
    
    if not bus_model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bus model not found"
        )
    
    return BusModelResponse.from_orm(bus_model)

@router.put("/bus-models/{bus_model_id}", response_model=BusModelResponse)
async def update_bus_model(
    bus_model_id: UUID,
    bus_model_data: BusModelCreate,
    current_user: User = Depends(verify_jwt_token),
    db: AsyncSession = Depends(get_db)
):
    """Update an existing bus model"""
    
    # Check if bus model exists and belongs to company
    result = await db.execute(
        select(BusModel).where(
            BusModel.id == bus_model_id,
            BusModel.company_id == current_user.company_id
        )
    )
    bus_model = result.scalar_one_or_none()
    
    if not bus_model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bus model not found"
        )
    
    try:
        # Update bus model
        await db.execute(
            update(BusModel)
            .where(BusModel.id == bus_model_id)
            .values(
                name=bus_model_data.name,
                manufacturer=bus_model_data.manufacturer,
                specs=bus_model_data.specs
            )
        )
        await db.commit()
        
        # Fetch updated model
        result = await db.execute(
            select(BusModel).where(BusModel.id == bus_model_id)
        )
        updated_model = result.scalar_one()
        
        return BusModelResponse.from_orm(updated_model)
        
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bus model with name '{bus_model_data.name}' already exists for your company"
        )

@router.delete("/bus-models/{bus_model_id}")
async def delete_bus_model(
    bus_model_id: UUID,
    current_user: User = Depends(verify_jwt_token),
    db: AsyncSession = Depends(get_db)
):
    """Delete a bus model (only if not used in fleet or simulations)"""
    
    # Check if bus model exists and belongs to company
    result = await db.execute(
        select(BusModel).where(
            BusModel.id == bus_model_id,
            BusModel.company_id == current_user.company_id
        )
    )
    bus_model = result.scalar_one_or_none()
    
    if not bus_model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bus model not found"
        )
    
    # Check if used in fleet inventory
    fleet_result = await db.execute(
        select(FleetInventory).where(FleetInventory.bus_model_id == bus_model_id)
    )
    if fleet_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete bus model: it's used in fleet inventory"
        )
    
    # Check if used in simulations
    sim_result = await db.execute(
        select(SimulationRun).where(SimulationRun.bus_model_id == bus_model_id)
    )
    if sim_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete bus model: it's used in simulations"
        )
    
    # Delete the bus model
    await db.execute(
        delete(BusModel).where(BusModel.id == bus_model_id)
    )
    await db.commit()
    
    return {"message": "Bus model deleted successfully"}

# ================================
# FLEET INVENTORY ENDPOINTS
# ================================

@router.get("/fleet-inventory", response_model=List[FleetInventoryResponse])
async def get_fleet_inventory(
    current_user: User = Depends(verify_jwt_token),
    db: AsyncSession = Depends(get_db)
):
    """Get fleet inventory for current user's company"""
    result = await db.execute(
        select(FleetInventory)
        .where(FleetInventory.company_id == current_user.company_id)
        .order_by(FleetInventory.quantity.desc())
    )
    inventory = result.scalars().all()
    
    # Add bus model details to each inventory item
    response_data = []
    for item in inventory:
        bus_model_result = await db.execute(
            select(BusModel).where(BusModel.id == item.bus_model_id)
        )
        bus_model = bus_model_result.scalar_one_or_none()
        
        response_item = FleetInventoryResponse.from_orm(item)
        if bus_model:
            response_item.bus_model = BusModelResponse.from_orm(bus_model)
        response_data.append(response_item)
    
    return response_data

@router.post("/fleet-inventory", response_model=FleetInventoryResponse)
async def create_fleet_inventory(
    inventory_data: FleetInventoryCreate,
    current_user: User = Depends(verify_jwt_token),
    db: AsyncSession = Depends(get_db)
):
    """
    Add buses to fleet inventory
    
    **Example Usage:**
    ```json
    {
        "bus_model_id": "550e8400-e29b-41d4-a716-446655440000",
        "quantity": 15
    }
    ```
    """
    
    # Verify bus model exists and belongs to user's company
    bus_model_result = await db.execute(
        select(BusModel).where(
            BusModel.id == inventory_data.bus_model_id,
            BusModel.company_id == current_user.company_id
        )
    )
    bus_model = bus_model_result.scalar_one_or_none()
    
    if not bus_model:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bus model not found or does not belong to your company"
        )
    
    # Check if inventory entry already exists for this model
    existing_result = await db.execute(
        select(FleetInventory).where(
            FleetInventory.company_id == current_user.company_id,
            FleetInventory.bus_model_id == inventory_data.bus_model_id
        )
    )
    existing_inventory = existing_result.scalar_one_or_none()
    
    if existing_inventory:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Fleet inventory already exists for this bus model. Current quantity: {existing_inventory.quantity}. Use PUT to update."
        )
    
    try:
        # Create new inventory entry
        db_inventory = FleetInventory(
            company_id=current_user.company_id,
            bus_model_id=inventory_data.bus_model_id,
            quantity=inventory_data.quantity
        )
        
        db.add(db_inventory)
        await db.commit()
        await db.refresh(db_inventory)
        
        # Return response with bus model details
        response = FleetInventoryResponse.from_orm(db_inventory)
        response.bus_model = BusModelResponse.from_orm(bus_model)
        
        return response
        
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Fleet inventory entry already exists for this bus model"
        )

@router.put("/fleet-inventory/{inventory_id}", response_model=FleetInventoryResponse)
async def update_fleet_inventory(
    inventory_id: UUID,
    inventory_data: FleetInventoryUpdate,
    current_user: User = Depends(verify_jwt_token),
    db: AsyncSession = Depends(get_db)
):
    """
    Update fleet inventory quantity
    
    **Example Usage:**
    ```json
    {
        "quantity": 20
    }
    ```
    """
    
    # Check if inventory exists and belongs to company
    result = await db.execute(
        select(FleetInventory).where(
            FleetInventory.id == inventory_id,
            FleetInventory.company_id == current_user.company_id
        )
    )
    inventory = result.scalar_one_or_none()
    
    if not inventory:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Fleet inventory entry not found"
        )
    
    # Update quantity
    await db.execute(
        update(FleetInventory)
        .where(FleetInventory.id == inventory_id)
        .values(quantity=inventory_data.quantity)
    )
    await db.commit()
    
    # Fetch updated inventory with bus model details
    result = await db.execute(
        select(FleetInventory).where(FleetInventory.id == inventory_id)
    )
    updated_inventory = result.scalar_one()
    
    bus_model_result = await db.execute(
        select(BusModel).where(BusModel.id == updated_inventory.bus_model_id)
    )
    bus_model = bus_model_result.scalar_one_or_none()
    
    response = FleetInventoryResponse.from_orm(updated_inventory)
    if bus_model:
        response.bus_model = BusModelResponse.from_orm(bus_model)
    
    return response

@router.delete("/fleet-inventory/{inventory_id}")
async def delete_fleet_inventory(
    inventory_id: UUID,
    current_user: User = Depends(verify_jwt_token),
    db: AsyncSession = Depends(get_db)
):
    """Remove buses from fleet inventory"""
    
    result = await db.execute(
        delete(FleetInventory).where(
            FleetInventory.id == inventory_id,
            FleetInventory.company_id == current_user.company_id
        )
    )
    
    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Fleet inventory entry not found"
        )
    
    await db.commit()
    return {"message": "Fleet inventory entry deleted successfully"}

# ================================
# SIMULATION ENDPOINTS (unchanged)
# ================================

@router.get("/simulations", response_model=List[SimulationResponse])
async def get_simulations(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    status: Optional[str] = Query(None, description="Filter by status"),
    bus_model_id: Optional[UUID] = Query(None, description="Filter by bus model"),
    current_user: User = Depends(verify_jwt_token),
    db: AsyncSession = Depends(get_db)
):
    """
    Get simulation runs for current user's company
    
    - **skip**: Number of records to skip
    - **limit**: Maximum number of records to return
    - **status**: Filter by simulation status (pending, running, completed, failed)
    - **bus_model_id**: Filter by specific bus model
    """
    query = select(SimulationRun).where(
        SimulationRun.company_id == current_user.company_id
    )
    
    if status:
        query = query.where(SimulationRun.status == status)
    
    if bus_model_id:
        query = query.where(SimulationRun.bus_model_id == bus_model_id)
    
    query = query.offset(skip).limit(limit).order_by(SimulationRun.created_at.desc())
    
    result = await db.execute(query)
    simulations = result.scalars().all()
    
    # Add bus model details to each simulation
    response_data = []
    for sim in simulations:
        bus_model_result = await db.execute(
            select(BusModel).where(BusModel.id == sim.bus_model_id)
        )
        bus_model = bus_model_result.scalar_one_or_none()
        
        response_item = SimulationResponse.from_orm(sim)
        if bus_model:
            response_item.bus_model = BusModelResponse.from_orm(bus_model)
        response_data.append(response_item)
    
    return response_data

@router.get("/simulations/{simulation_id}", response_model=SimulationResponse)
async def get_simulation(
    simulation_id: UUID,
    current_user: User = Depends(verify_jwt_token),
    db: AsyncSession = Depends(get_db)
):
    """Get specific simulation run by ID"""
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
    
    # Add bus model details
    bus_model_result = await db.execute(
        select(BusModel).where(BusModel.id == simulation.bus_model_id)
    )
    bus_model = bus_model_result.scalar_one_or_none()
    
    response = SimulationResponse.from_orm(simulation)
    if bus_model:
        response.bus_model = BusModelResponse.from_orm(bus_model)
    
    return response

@router.get("/simulations-summary", response_model=SimulationSummary)
async def get_simulations_summary(
    current_user: User = Depends(verify_jwt_token),
    db: AsyncSession = Depends(get_db)
):
    """Get summary statistics for simulations"""
    
    # Get all simulations for the company
    result = await db.execute(
        select(SimulationRun).where(
            SimulationRun.company_id == current_user.company_id
        )
    )
    simulations = result.scalars().all()
    
    total = len(simulations)
    completed = len([s for s in simulations if s.status == "completed"])
    pending = len([s for s in simulations if s.status == "pending"])
    failed = len([s for s in simulations if s.status == "failed"])
    
    # Calculate average battery size for completed simulations
    completed_sims = [s for s in simulations if s.status == "completed" and s.optimal_battery_kwh]
    avg_battery = None
    if completed_sims:
        avg_battery = sum(float(s.optimal_battery_kwh) for s in completed_sims) / len(completed_sims)
    
    return SimulationSummary(
        total_simulations=total,
        completed_simulations=completed,
        pending_simulations=pending,
        failed_simulations=failed,
        average_battery_size=avg_battery,
        company_id=current_user.company_id
    ) 