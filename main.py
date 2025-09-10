"""
Elettra Backend - Main FastAPI Application
"""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from app.routers import agency, auth, gtfs, simulation
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
    yield
    # Shutdown
    logger.info(f"🔌 {settings.app_name} shutting down...")

# FastAPI app instance
app = FastAPI(
    title=settings.app_name,
    description="""
    ## Elettra - Swiss Public Transport Electrification Tool
    
    Backend API for GTFS data management and related operations.
    
    ### Main Features:
    - **GTFS Data Management**: Agencies, Routes, Stops
    - **Bus Models Management**: Bus specifications and configurations
    - **User Management**: Access control and authentication
    - **Simulation Management**: Run and track simulations
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
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Include authentication and API routers
app.include_router(auth.router, prefix="/auth", tags=["Authentication"])
app.include_router(agency.router, prefix="/api/v1/agency", tags=["Agency"])
app.include_router(gtfs.router, prefix="/api/v1/gtfs", tags=["GTFS"])
app.include_router(simulation.router, prefix="/api/v1/simulation", tags=["Simulation"])

# Fallback CORS headers for tools/tests that don't send Origin (debug only)
if settings.debug:
    @app.middleware("http")
    async def add_default_cors_headers(request, call_next):
        response = await call_next(request)
        # If standard CORS middleware did not add headers (no Origin supplied), add permissive ones for dev/testing
        if 'access-control-allow-origin' not in (k.lower() for k in response.headers.keys()):
            response.headers['Access-Control-Allow-Origin'] = '*'
            response.headers['Access-Control-Allow-Methods'] = 'GET,POST,PUT,DELETE,OPTIONS'
            response.headers['Access-Control-Allow-Headers'] = '*'
        return response

@app.get("/", tags=["Root"])
async def root():
    """Root endpoint - API status"""
    return {
        "message": settings.app_name,
        "status": "running",
        "version": settings.app_version
    }

if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port)
