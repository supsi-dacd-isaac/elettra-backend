"""
Pydantic schemas for request/response models
"""

from pydantic import BaseModel, EmailStr, Field, validator
from typing import Optional, Dict, Any, List
from datetime import datetime
from uuid import UUID
from enum import Enum

class UserRole(str, Enum):
    ADMIN = "admin"
    ANALYST = "analyst"
    VIEWER = "viewer"

class SimulationStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

# User schemas
class UserResponse(BaseModel):
    id: UUID
    company_id: UUID
    email: EmailStr
    full_name: str
    role: UserRole
    created_at: datetime
    
    class Config:
        from_attributes = True

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse

# Company schemas
class CompanyResponse(BaseModel):
    id: UUID
    name: str
    created_at: datetime
    
    class Config:
        from_attributes = True

# Bus Model schemas
class BusModelCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Bus model name")
    manufacturer: Optional[str] = Field(None, max_length=255, description="Manufacturer name")
    specs: Dict[str, Any] = Field(
        default_factory=dict, 
        description="Bus specifications as JSON",
        example={
            "passenger_capacity": 85,
            "length_m": 12.0,
            "width_m": 2.55,
            "height_m": 3.2,
            "weight_kg": 12500,
            "max_speed_kmh": 80,
            "fuel_type": "electric",
            "doors": 3,
            "accessibility": True,
            "air_conditioning": True
        }
    )

class BusModelResponse(BaseModel):
    id: UUID
    company_id: UUID
    name: str
    manufacturer: Optional[str]
    specs: Dict[str, Any]
    
    class Config:
        from_attributes = True

# Fleet Inventory schemas
class FleetInventoryCreate(BaseModel):
    bus_model_id: UUID = Field(..., description="ID of the bus model")
    quantity: int = Field(..., ge=0, description="Number of buses (0 or positive)")
    
    @validator('quantity')
    def validate_quantity(cls, v):
        if v < 0:
            raise ValueError('Quantity must be 0 or positive')
        return v

class FleetInventoryUpdate(BaseModel):
    quantity: int = Field(..., ge=0, description="Updated number of buses")
    
    @validator('quantity')
    def validate_quantity(cls, v):
        if v < 0:
            raise ValueError('Quantity must be 0 or positive')
        return v

class FleetInventoryResponse(BaseModel):
    id: UUID
    company_id: UUID
    bus_model_id: UUID
    quantity: int
    bus_model: Optional[BusModelResponse] = None
    
    class Config:
        from_attributes = True

# Simulation schemas
class SimulationInput(BaseModel):
    """Input for battery optimization simulation with flexible parameters"""
    bus_model_id: UUID = Field(..., description="ID of the bus model to simulate")
    input_params: Dict[str, Any] = Field(
        ..., 
        description="Flexible dictionary containing all simulation parameters",
        example={
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
    )

class SimulationResponse(BaseModel):
    id: UUID
    company_id: UUID
    user_id: UUID
    bus_model_id: UUID
    input_params: Dict[str, Any]
    optimal_battery_kwh: Optional[float]
    output_results: Optional[Dict[str, Any]]
    status: SimulationStatus
    created_at: datetime
    completed_at: Optional[datetime]
    bus_model: Optional[BusModelResponse] = None
    
    class Config:
        from_attributes = True

class SimulationSummary(BaseModel):
    """Summary statistics for simulations"""
    total_simulations: int
    completed_simulations: int
    pending_simulations: int
    failed_simulations: int
    average_battery_size: Optional[float]
    company_id: UUID 