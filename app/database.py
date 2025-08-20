"""
Database configuration and models for existing Elettra database
"""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy import Column, String, DateTime, UUID, Text, Integer, Boolean, Numeric, ForeignKey, JSON, select
from sqlalchemy.dialects.postgresql import JSONB, ENUM as PG_ENUM
from sqlalchemy.sql import func
import uuid
from enum import Enum as PyEnum
from app.core.config import get_cached_settings
from datetime import datetime

# Base class for all models
Base = declarative_base()

# Database enums
sim_status_enum = PG_ENUM('pending', 'running', 'completed', 'failed', name='sim_status', create_type=False)

class GTFSDatasetStatus(PyEnum):
    PENDING = "pending"
    DOWNLOADING = "downloading" 
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

class Company(Base):
    """Company model"""
    __tablename__ = "companies"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False, unique=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    
    # Relationships
    users = relationship("User", back_populates="company")
    bus_models = relationship("BusModel", back_populates="company")
    fleet_inventory = relationship("FleetInventory", back_populates="company")
    simulation_runs = relationship("SimulationRun", back_populates="company")
    gtfs_datasets = relationship("GTFSDataset", back_populates="company")
    transit_routes = relationship("TransitRoute", back_populates="company")

class User(Base):
    """User model matching the existing schema"""
    __tablename__ = "users"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    email = Column(String, nullable=False, unique=True)
    full_name = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False)  # 'admin', 'analyst', 'viewer'
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    
    # Relationships
    company = relationship("Company", back_populates="users")
    simulation_runs = relationship("SimulationRun", back_populates="user")

class BusModel(Base):
    """Bus models (templates) table"""
    __tablename__ = "bus_models"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    name = Column(String, nullable=False)
    manufacturer = Column(String)
    specs = Column(JSON, nullable=False, default={})
    
    # Relationships
    company = relationship("Company", back_populates="bus_models")
    fleet_inventory = relationship("FleetInventory", back_populates="bus_model")
    simulation_runs = relationship("SimulationRun", back_populates="bus_model")

class FleetInventory(Base):
    """Fleet inventory - quantity of buses per model per company"""
    __tablename__ = "fleet_inventory"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    bus_model_id = Column(UUID(as_uuid=True), ForeignKey("bus_models.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    
    # Relationships
    company = relationship("Company", back_populates="fleet_inventory")
    bus_model = relationship("BusModel", back_populates="fleet_inventory")

class SimulationRun(Base):
    """Simulation runs for battery optimization"""
    __tablename__ = "simulation_runs"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    bus_model_id = Column(UUID(as_uuid=True), ForeignKey("bus_models.id"), nullable=False)
    transit_route_id = Column(UUID(as_uuid=True), ForeignKey("transit_routes.id"), nullable=True)
    route_segment_start_stop = Column(UUID(as_uuid=True), ForeignKey("route_stops.id"), nullable=True)
    route_segment_end_stop = Column(UUID(as_uuid=True), ForeignKey("route_stops.id"), nullable=True)
    
    input_params = Column(JSON, nullable=False)
    optimal_battery_kwh = Column(Numeric)
    output_results = Column(JSON)
    
    status = Column(sim_status_enum, nullable=False, default='pending')
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime)
    
    # Relationships
    company = relationship("Company", back_populates="simulation_runs")
    user = relationship("User", back_populates="simulation_runs")
    bus_model = relationship("BusModel", back_populates="simulation_runs")
    transit_route = relationship("TransitRoute", back_populates="simulation_runs")
    start_stop = relationship("RouteStop", foreign_keys=[route_segment_start_stop])
    end_stop = relationship("RouteStop", foreign_keys=[route_segment_end_stop])

# New models for GTFS and route data
class GTFSDataset(Base):
    __tablename__ = "gtfs_datasets"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    dataset_name = Column(String, nullable=False)
    gtfs_url = Column(String)
    osm_extract_url = Column(String)
    last_updated = Column(DateTime)
    status = Column(String, nullable=False, default=GTFSDatasetStatus.PENDING)
    file_path = Column(String)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    
    # Relationships
    company = relationship("Company", back_populates="gtfs_datasets")
    transit_routes = relationship("TransitRoute", back_populates="gtfs_dataset")

class TransitRoute(Base):
    __tablename__ = "transit_routes"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False)
    gtfs_dataset_id = Column(UUID(as_uuid=True), ForeignKey("gtfs_datasets.id"), nullable=False)
    gtfs_route_id = Column(String, nullable=False)
    route_short_name = Column(String)
    route_long_name = Column(String)
    route_type = Column(Integer, nullable=False)
    route_color = Column(String)
    agency_name = Column(String)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    
    # Relationships
    company = relationship("Company", back_populates="transit_routes")
    gtfs_dataset = relationship("GTFSDataset", back_populates="transit_routes")
    route_shapes = relationship("RouteShape", back_populates="transit_route")
    route_stops = relationship("RouteStop", back_populates="transit_route")
    simulation_runs = relationship("SimulationRun", back_populates="transit_route")

class RouteShape(Base):
    __tablename__ = "route_shapes"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transit_route_id = Column(UUID(as_uuid=True), ForeignKey("transit_routes.id"), nullable=False)
    gtfs_shape_id = Column(String, nullable=False)
    shape_geom = Column(String)  # Will store as WKT string for now
    total_distance_m = Column(Numeric)
    elevation_gain_m = Column(Numeric)
    avg_grade_percent = Column(Numeric)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    
    # Relationships
    transit_route = relationship("TransitRoute", back_populates="route_shapes")

class RouteStop(Base):
    __tablename__ = "route_stops"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transit_route_id = Column(UUID(as_uuid=True), ForeignKey("transit_routes.id"), nullable=False)
    gtfs_stop_id = Column(String, nullable=False)
    stop_name = Column(String, nullable=False)
    stop_lat = Column(Numeric, nullable=False)
    stop_lon = Column(Numeric, nullable=False)
    stop_sequence = Column(Integer, nullable=False)
    distance_from_start_m = Column(Numeric)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    
    # Relationships
    transit_route = relationship("TransitRoute", back_populates="route_stops")

# Initialize database connection
def get_database_url() -> str:
    """Get database URL from settings"""
    settings = get_cached_settings()
    return settings.get_database_url()

def get_sync_database_url():
    """Get synchronous database URL for migrations"""
    settings = get_cached_settings()
    return f"postgresql://{settings.database_url}"

# Create async engine
engine = create_async_engine(
    get_database_url(),
    echo=get_cached_settings().database_echo,
    future=True
)

# Create async session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False
)

async def get_async_session() -> AsyncSession:
    """Dependency to get async database session"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
