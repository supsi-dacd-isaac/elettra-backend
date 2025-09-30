"""
Elettra Backend - Main FastAPI Application
"""

import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, UTC
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn
import textwrap
from app.routers import agency, auth, gtfs, simulation, user as user_router
from app.core.config import get_cached_settings
from app.schemas.health import HealthCheckResponse, ServiceStatus
from app.database import get_async_session
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
import re

# Configure logging early
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_cached_settings()

# Track application startup time for uptime calculation
startup_time = time.time()

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
    description=textwrap.dedent(
        """
        # Elettra - Swiss Public Bus Electrification Tool
        
        Comprehensive backend API for public transport electrification planning and simulation.
        
        ## Core Features
        
        ### Authentication & User Management
        - JWT-based authentication system
        - User registration, login, and profile management
        - Role-based access control (admin, analyst, user)
        - Agency-level user management
        
        ### GTFS Data Management
        - Agencies: Transit agency management
        - Routes: GTFS route definitions and variants
        - Trips: Trip planning with auxiliary trips (depot, transfer, service)
        - Stops: Stop management and stop times
        - Calendar: Service calendar management
        - Variants: Route variant analysis
        
        ### Fleet Management
        - Bus Models: Electric bus specifications and configurations
        - Buses: Fleet management with depot assignments
        - Depots: Depot locations and capacity management
        - Shifts: Shift planning and scheduling
        
        ### Simulation & Analysis
        - Simulation Runs: Electrification simulation execution
        - Results Analysis: Simulation outcome analysis
        - Weather Integration: PVGIS TMY weather data
        - Elevation Profiles: SwissTopo elevation data integration
        
        ### External Services Integration
        - OSRM Routing: Driving distance calculations
        - SwissTopo: Elevation profile generation
        - PVGIS: Weather data for solar calculations
        - MinIO: File storage for elevation profiles
        
        ## Technical Features
        - Async PostgreSQL database operations
        - Comprehensive health monitoring
        - CORS support for frontend integration
        - Detailed API documentation with Swagger UI
        - Error handling and validation
        """
    ).strip(),
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
app.include_router(user_router.router, prefix="/api/v1/user", tags=["User"])
app.include_router(gtfs.router, prefix="/api/v1/gtfs", tags=["GTFS"])
app.include_router(simulation.router, prefix="/api/v1/simulation", tags=["Simulation"])

# ----------------------------------------------------------------------------
# Global error handlers
# ----------------------------------------------------------------------------

_SQLSTATE_MAP = {
    # Constraint violations
    "23505": ("unique_violation", 409, "Unique constraint violated"),
    "23503": ("foreign_key_violation", 409, "Foreign key constraint violated"),
    "23502": ("not_null_violation", 400, "Required column is null"),
    "23514": ("check_violation", 400, "Check constraint violated"),
    # Data issues
    "22001": ("string_data_right_truncation", 400, "Value too long for column"),
}


def _extract_sqlstate(exc: IntegrityError) -> str | None:
    orig = getattr(exc, "orig", None)
    if orig is None:
        return None
    # asyncpg exposes sqlstate; psycopg exposes pgcode
    return getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)


def _extract_constraint(exc: IntegrityError) -> str | None:
    orig = getattr(exc, "orig", None)
    return getattr(orig, "constraint_name", None) or None


def _extract_detail_and_fields(exc: IntegrityError) -> tuple[str | None, list[str] | None]:
    orig = getattr(exc, "orig", None)
    detail = None
    fields: list[str] | None = None
    if orig is not None:
        # asyncpg/psycopg typically provide a detail attribute with
        # messages like: "Key (name)=(AA_NF) already exists."
        detail = getattr(orig, "detail", None)
    if not detail:
        # Fallback to stringified original exception
        detail = str(orig or exc)

    # Try to parse affected columns from the detail
    # Pattern: Key (col1, col2)=(..., ...) already exists
    try:
        m = re.search(r"Key \((?P<cols>[^\)]+)\)=\(", detail or "")
        if m:
            cols = [c.strip() for c in m.group("cols").split(",")]
            fields = cols if cols else None
    except Exception:
        fields = None
    return detail, fields


@app.exception_handler(IntegrityError)
async def handle_integrity_error(request: Request, exc: IntegrityError):
    sqlstate = _extract_sqlstate(exc)
    code, status_code, message = _SQLSTATE_MAP.get(sqlstate, ("integrity_error", 400, "Database integrity error"))

    constraint = _extract_constraint(exc)
    detail, fields = _extract_detail_and_fields(exc)

    logger.warning(
        "IntegrityError: path=%s sqlstate=%s constraint=%s detail=%s",
        request.url.path,
        sqlstate,
        constraint,
        detail,
    )

    payload = {
        "code": code,
        "message": message,
        "constraint": constraint,
        "fields": fields,
        "detail": detail,
    }
    return JSONResponse(status_code=status_code, content=payload)

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


@app.get("/health", response_model=HealthCheckResponse, tags=["Health"])
async def health_check():
    """
    Health check endpoint for monitoring and load balancers.
    
    Returns the overall health status of the application and its dependencies.
    """
    timestamp = datetime.now(UTC)
    uptime_seconds = time.time() - startup_time
    
    services = {}
    overall_status = "healthy"
    
    # Check database connectivity
    try:
        start_time = time.time()
        async for session in get_async_session():
            result = await session.execute(text("SELECT 1"))
            result.fetchone()
            response_time = (time.time() - start_time) * 1000
            services["database"] = ServiceStatus(
                status="healthy",
                message="Database connection successful",
                response_time_ms=round(response_time, 2),
                last_checked=timestamp
            )
            break
    except Exception as e:
        services["database"] = ServiceStatus(
            status="unhealthy",
            message=f"Database connection failed: {str(e)}",
            last_checked=timestamp
        )
        overall_status = "unhealthy"
    
    # Check external services (optional - can be extended)
    # For now, we'll just check if the application is running
    services["application"] = ServiceStatus(
        status="healthy",
        message="Application is running",
        last_checked=timestamp
    )
    
    # Determine overall status
    if any(service.status == "unhealthy" for service in services.values()):
        overall_status = "unhealthy"
    elif any(service.status == "degraded" for service in services.values()):
        overall_status = "degraded"
    
    return HealthCheckResponse(
        status=overall_status,
        timestamp=timestamp,
        version=settings.app_version,
        services=services,
        uptime_seconds=round(uptime_seconds, 2)
    )

if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port)
