"""
Elettra Backend - Main FastAPI Application

Focused on simulation runs and data retrieval for Swiss public transport
bus electrification analysis with flexible parameter handling.
"""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import yaml

with open('config/elettra-config.yaml') as f:
    config = yaml.safe_load(f)

host = config.get('host', '127.0.0.1')
port = config.get('port', 8000)

import uvicorn

from app.routers import auth, data, simulations, algorithms, routes
from app.core.config import get_cached_settings

# Configure logging early
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_cached_settings()

# Configure logging with settings
logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format=settings.log_format,
    force=True
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan management"""
    # Startup
    logger.info(f"🚌 {settings.app_name} v{settings.app_version} starting...")
    logger.info(f"Debug mode: {settings.debug}")
    logger.info(f"Database URL: {settings.database_url.split('@')[1] if '@' in settings.database_url else 'localhost'}")
    logger.info("Ready for flexible simulations!")
    yield
    # Shutdown
    logger.info(f"🔌 {settings.app_name} shutting down...")

# FastAPI app instance
app = FastAPI(
    title=settings.app_name,
    description="""
    ## Elettra - Swiss Public Transport Electrification Tool
    
    Backend API for battery optimization simulations with flexible parameter handling.
    
    ### Main Features:
    - **Flexible Simulation Engine**: Generic JSON-based parameter system for easy extension
    - **Battery Optimization**: Advanced algorithms considering Swiss conditions
    - **Data Management**: Bus models, fleet inventory, and simulation results
    - **Company Isolation**: Secure access to company-specific data only
    - **External Configuration**: Configurable via external YAML/JSON files
    
    ### Configuration:
    This application supports external configuration files (YAML or JSON format).
    Set the `ELETTRA_CONFIG_FILE` environment variable to specify the config file path.
    
    ### Security:
    This API is designed to be accessed exclusively by the Elettra frontend application.
    """,
    version=settings.app_version,
    debug=settings.debug,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    swagger_ui_parameters={"persistAuthorization": "true"}
)

# CORS middleware for frontend communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth.router, prefix="/api/v1/auth", tags=["Authentication"])
app.include_router(data.router, prefix="/api/v1/data", tags=["Data Management"])
app.include_router(simulations.router, prefix="/api/v1/simulations", tags=["Simulations"])
app.include_router(algorithms.router, prefix="/api/v1/algorithms", tags=["Algorithms"])
app.include_router(routes.router, prefix="/api/v1/routes", tags=["Swiss Transit Routes & GTFS"])

@app.get("/", tags=["Root"])
async def root():
    """Root endpoint - API status"""
    return {
        "message": settings.app_name,
        "description": "Swiss Public Transport Bus Electrification Analysis Tool",
        "version": settings.app_version,
        "status": "operational",
        "docs": "/docs",
        "features": [
            "Flexible simulation parameters",
            "Battery optimization algorithms", 
            "Swiss market analysis",
            "Company data isolation",
            "External configuration support",
            "Energy consumption analysis", 
            "Battery sizing optimization",
            "Swiss GTFS data processing with pfaedle",
            "Real route-based simulations",
            "Secure JWT authentication"
        ]
    }

@app.get("/health", tags=["Health"])
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": settings.app_name,
        "version": settings.app_version,
        "simulation_engine": "active",
        "max_concurrent_simulations": settings.max_concurrent_simulations
    }

@app.get("/config", tags=["Configuration"])
async def get_config_info():
    """Get non-sensitive configuration information"""
    return {
        "app_name": settings.app_name,
        "version": settings.app_version,
        "debug": settings.debug,
        "max_route_length_km": settings.max_route_length_km,
        "max_bus_capacity": settings.max_bus_capacity,
        "battery_efficiency_factor": settings.battery_efficiency_factor,
        "max_concurrent_simulations": settings.max_concurrent_simulations,
        "simulation_timeout_minutes": settings.simulation_timeout_minutes,
        "log_level": settings.log_level
    }

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=settings.reload,
        log_level=settings.log_level.lower()
    ) 