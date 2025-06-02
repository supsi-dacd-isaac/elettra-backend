"""
Database configuration and models for existing Elettra database
"""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy import Column, String, DateTime, UUID, Text, Integer, Boolean, Numeric, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB, ENUM
from sqlalchemy.sql import func
import uuid
from enum import Enum as PyEnum
from app.core.config import get_cached_settings

# Base class for all models
Base = declarative_base()

class SimStatus(PyEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

class Company(Base):
    """Company model"""
    __tablename__ = "companies"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(Text, nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class User(Base):
    """User model matching the existing schema"""
    __tablename__ = "users"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), ForeignKey('companies.id', ondelete='CASCADE'), nullable=False)
    email = Column(Text, nullable=False, unique=True)
    full_name = Column(Text, nullable=False)
    password_hash = Column(Text, nullable=False)
    role = Column(Text, nullable=False)  # 'admin', 'analyst', 'viewer'
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class BusModel(Base):
    """Bus models (templates) table"""
    __tablename__ = "bus_models"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), ForeignKey('companies.id', ondelete='CASCADE'), nullable=False)
    name = Column(Text, nullable=False)
    manufacturer = Column(Text)
    specs = Column(JSONB, nullable=False, default=dict)

class FleetInventory(Base):
    """Fleet inventory - quantity of buses per model per company"""
    __tablename__ = "fleet_inventory"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), ForeignKey('companies.id', ondelete='CASCADE'), nullable=False)
    bus_model_id = Column(UUID(as_uuid=True), ForeignKey('bus_models.id'), nullable=False)
    quantity = Column(Integer, nullable=False)

class SimulationRun(Base):
    """Simulation runs for battery optimization"""
    __tablename__ = "simulation_runs"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id = Column(UUID(as_uuid=True), ForeignKey('companies.id', ondelete='CASCADE'), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='SET NULL'), nullable=False)
    bus_model_id = Column(UUID(as_uuid=True), ForeignKey('bus_models.id'), nullable=False)
    
    input_params = Column(JSONB, nullable=False)
    optimal_battery_kwh = Column(Numeric)
    output_results = Column(JSONB)
    
    status = Column(ENUM(SimStatus), nullable=False, default=SimStatus.PENDING)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True))

# Initialize database connection
def get_database_url() -> str:
    """Get database URL from settings"""
    settings = get_cached_settings()
    return settings.get_database_url()

# Create async engine
engine = create_async_engine(
    get_database_url(),
    echo=False,  # Set to True for SQL debugging
    pool_pre_ping=True
)

# Create session maker
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)

async def get_db() -> AsyncSession:
    """Dependency to get database session"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close() 