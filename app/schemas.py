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

# New schemas for GTFS and route data
class GTFSDatasetStatus(str, Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

class GTFSDatasetCreate(BaseModel):
    dataset_name: str = Field(..., description="Name for this GTFS dataset")
    gtfs_url: Optional[str] = Field(None, description="URL to download GTFS data")
    osm_extract_url: Optional[str] = Field(None, description="URL to OSM extract for region")

class GTFSDatasetResponse(BaseModel):
    id: UUID
    company_id: UUID
    dataset_name: str
    gtfs_url: Optional[str]
    osm_extract_url: Optional[str]
    last_updated: Optional[datetime]
    status: GTFSDatasetStatus
    file_path: Optional[str]
    created_at: datetime
    
    class Config:
        from_attributes = True

class TransitRouteResponse(BaseModel):
    id: UUID
    company_id: UUID
    gtfs_dataset_id: UUID
    gtfs_route_id: str
    route_short_name: Optional[str]
    route_long_name: Optional[str]
    route_type: int
    route_color: Optional[str]
    agency_name: Optional[str]
    created_at: datetime
    
    class Config:
        from_attributes = True

class RouteStopResponse(BaseModel):
    id: UUID
    transit_route_id: UUID
    gtfs_stop_id: str
    stop_name: str
    stop_lat: float
    stop_lon: float
    stop_sequence: int
    distance_from_start_m: Optional[float]
    created_at: datetime
    
    class Config:
        from_attributes = True

class RouteShapeResponse(BaseModel):
    id: UUID
    transit_route_id: UUID
    gtfs_shape_id: str
    shape_geom: Optional[str]  # WKT string
    total_distance_m: Optional[float]
    elevation_gain_m: Optional[float]
    avg_grade_percent: Optional[float]
    created_at: datetime
    
    class Config:
        from_attributes = True

class TransitRouteDetailResponse(TransitRouteResponse):
    """Extended route response with stops and shapes"""
    route_stops: List[RouteStopResponse] = []
    route_shapes: List[RouteShapeResponse] = []

# Swiss transport company presets for GTFS datasets
class SwissTransportCompany(BaseModel):
    name: str
    gtfs_url: Optional[str]
    osm_region: str
    description: str

# Algorithm schemas (existing)
class BusType(str, Enum):
    MINI = "mini"
    STANDARD = "standard"
    ARTICULATED = "articulated"
    DOUBLE_DECKER = "double_decker"

class EnergyConsumptionInput(BaseModel):
    route_length_km: float = Field(..., gt=0, description="Total route length in kilometers")
    bus_type: BusType = Field(..., description="Type of bus")
    passenger_capacity: int = Field(..., gt=0, description="Maximum passenger capacity")
    average_speed_kmh: float = Field(..., gt=0, description="Average operating speed in km/h")
    terrain_factor: float = Field(1.0, ge=0.5, le=2.0, description="Terrain difficulty factor (1.0 = flat)")
    climate_factor: float = Field(1.0, ge=0.8, le=1.5, description="Climate impact factor (1.0 = moderate)")

class EnergyConsumptionResult(BaseModel):
    energy_consumption_kwh_per_100km: float
    daily_energy_requirement_kwh: float
    monthly_energy_requirement_kwh: float
    annual_energy_requirement_kwh: float
    calculation_parameters: Dict[str, Any]

class BatterySizingInput(BaseModel):
    daily_energy_requirement_kwh: float = Field(..., gt=0, description="Daily energy requirement in kWh")
    safety_margin: float = Field(0.25, ge=0.1, le=0.5, description="Safety margin (25% recommended)")
    degradation_factor: float = Field(0.85, ge=0.7, le=1.0, description="Battery degradation factor")
    charging_efficiency: float = Field(0.92, ge=0.8, le=1.0, description="Charging efficiency")

class BatterySizingResult(BaseModel):
    recommended_battery_capacity_kwh: float
    minimum_battery_capacity_kwh: float
    battery_cost_estimate_chf: float
    charging_time_hours: float
    battery_weight_kg: float

# GTFS processing schemas
class GTFSProcessingRequest(BaseModel):
    dataset_id: UUID = Field(..., description="ID of the GTFS dataset to process")
    process_with_pfaedle: bool = Field(True, description="Whether to enhance shapes with pfaedle")
    filter_route_types: Optional[List[int]] = Field(None, description="Filter specific route types (3=bus, 1=subway, etc.)")

class GTFSProcessingResult(BaseModel):
    dataset_id: UUID
    total_routes: int
    processed_routes: int
    total_stops: int
    total_shapes: int
    processing_time_seconds: float
    errors: List[str] = [] 