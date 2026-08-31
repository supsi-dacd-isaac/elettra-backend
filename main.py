"""
Elettra Backend - Main FastAPI Application
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, UTC
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn
import textwrap
from app.routers import agency, auth, economic, environmental, gtfs, simulation, user as user_router, yearly_analysis
from app.core.config import get_cached_settings
from app.schemas.health import HealthCheckResponse, ServiceStatus
from app.database import AsyncSessionLocal, get_async_session
from app.services.elevation_profiles import (
    configured_release,
    create_minio_client,
    elevation_release_runtime_metadata,
    elevation_profiles_bucket,
    gtfs_elevation_profiles_bucket,
    probe_configured_release_immutable,
    validate_configured_release,
    validate_production_profile_contract,
    validate_release_covers_database,
)
from app.services.runtime_release import runtime_release_configuration
from app.services.model_release import (
    model_release_runtime_metadata,
    probe_configured_model_immutable,
    validate_configured_model_release,
)
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
import re

# Configure logging early
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_cached_settings()

# Track application startup time for uptime calculation
startup_time = time.time()

_ELEVATION_JOB_COLUMNS = {
    "id",
    "trip_id",
    "payload",
    "status",
    "attempts",
    "available_at",
    "lease_expires_at",
    "worker_id",
    "last_error",
    "algorithm_version",
    "roads_release",
    "output_object_name",
    "created_at",
    "updated_at",
    "completed_at",
}
_ELEVATION_JOB_CONSTRAINTS = {
    "elevation_profile_jobs_pkey",
    "elevation_profile_jobs_trip_id_key",
    "elevation_profile_jobs_trip_id_fkey",
    "elevation_profile_jobs_status_check",
    "elevation_profile_jobs_attempts_check",
}
_ELEVATION_JOB_INDEX = "elevation_profile_jobs_status_available_at_idx"
_CLEANUP_JOB_COLUMNS = {
    "id",
    "trip_id",
    "payload",
    "status",
    "attempts",
    "available_at",
    "lease_expires_at",
    "worker_id",
    "last_error",
    "created_at",
    "updated_at",
    "completed_at",
}
_CLEANUP_JOB_CONSTRAINTS = {
    "elevation_profile_cleanup_jobs_pkey",
    "elevation_profile_cleanup_jobs_trip_id_key",
    "elevation_profile_cleanup_jobs_status_check",
    "elevation_profile_cleanup_jobs_attempts_check",
}
_CLEANUP_JOB_INDEX = "elevation_profile_cleanup_jobs_status_available_at_idx"
_HYBRID_WEATHER_COLUMNS = {
    "id",
    "latitude",
    "longitude",
    "requested_latitude",
    "requested_longitude",
    "provider",
    "openmeteo_model",
    "processing_version",
    "status",
    "pvgis_months_selected",
    "pvgis_metadata",
    "openmeteo_metadata",
    "row_count",
    "generated_at",
    "applied_at",
    "rolled_back_at",
}
_HYBRID_WEATHER_INDEX = "weather_temperature_series_active_coordinate_udx"
_PREDICTION_STACK_COLUMNS = {"prediction_stack", "auxiliary_estimator_release"}
_TRIP_COMPONENT_COLUMNS = {"component_breakdown"}
_PREDICTION_STACK_CONSTRAINT = "prediction_runs_prediction_stack_check"


async def validate_elevation_jobs_schema(session) -> None:
    """Fail when a required durable schema migration is missing or partial."""
    columns_result = await session.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'elevation_profile_jobs'
            """
        )
    )
    columns = set(columns_result.scalars().all())
    missing_columns = sorted(_ELEVATION_JOB_COLUMNS - columns)
    if missing_columns:
        raise RuntimeError(
            "migration 005 is missing or incomplete; elevation_profile_jobs "
            f"lacks columns: {', '.join(missing_columns)}"
        )

    constraints_result = await session.execute(
        text(
            """
            SELECT conname
            FROM pg_constraint
            WHERE conrelid = 'public.elevation_profile_jobs'::regclass
            """
        )
    )
    constraints = set(constraints_result.scalars().all())
    missing_constraints = sorted(_ELEVATION_JOB_CONSTRAINTS - constraints)
    if missing_constraints:
        raise RuntimeError(
            "migration 005 is incomplete; elevation_profile_jobs lacks constraints: "
            + ", ".join(missing_constraints)
        )

    index_result = await session.execute(
        text(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename = 'elevation_profile_jobs'
            """
        )
    )
    indexes = set(index_result.scalars().all())
    if _ELEVATION_JOB_INDEX not in indexes:
        raise RuntimeError(
            "migration 005 is incomplete; elevation_profile_jobs lacks index "
            f"{_ELEVATION_JOB_INDEX}"
        )

    cleanup_columns_result = await session.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'elevation_profile_cleanup_jobs'
            """
        )
    )
    cleanup_columns = set(cleanup_columns_result.scalars().all())
    missing_cleanup_columns = sorted(_CLEANUP_JOB_COLUMNS - cleanup_columns)
    if missing_cleanup_columns:
        raise RuntimeError(
            "migration 006 is missing or incomplete; elevation_profile_cleanup_jobs "
            f"lacks columns: {', '.join(missing_cleanup_columns)}"
        )

    cleanup_constraints_result = await session.execute(
        text(
            """
            SELECT conname
            FROM pg_constraint
            WHERE conrelid = 'public.elevation_profile_cleanup_jobs'::regclass
            """
        )
    )
    cleanup_constraints = set(cleanup_constraints_result.scalars().all())
    missing_cleanup_constraints = sorted(
        _CLEANUP_JOB_CONSTRAINTS - cleanup_constraints
    )
    if missing_cleanup_constraints:
        raise RuntimeError(
            "migration 006 is incomplete; elevation_profile_cleanup_jobs lacks "
            "constraints: " + ", ".join(missing_cleanup_constraints)
        )
    cleanup_fk_result = await session.execute(
        text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conrelid = 'public.elevation_profile_cleanup_jobs'::regclass
                  AND contype = 'f'
            )
            """
        )
    )
    if bool(cleanup_fk_result.scalar_one()):
        raise RuntimeError(
            "migration 006 is invalid; elevation_profile_cleanup_jobs must not "
            "have a foreign key because the outbox must survive trip deletion"
        )

    cleanup_index_result = await session.execute(
        text(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename = 'elevation_profile_cleanup_jobs'
            """
        )
    )
    cleanup_indexes = set(cleanup_index_result.scalars().all())
    if _CLEANUP_JOB_INDEX not in cleanup_indexes:
        raise RuntimeError(
            "migration 006 is incomplete; elevation_profile_cleanup_jobs lacks index "
            f"{_CLEANUP_JOB_INDEX}"
        )

    weather_columns_result = await session.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'weather_temperature_series'
            """
        )
    )
    weather_columns = set(weather_columns_result.scalars().all())
    missing_weather_columns = sorted(_HYBRID_WEATHER_COLUMNS - weather_columns)
    if missing_weather_columns:
        raise RuntimeError(
            "migration 007 is missing or incomplete; weather_temperature_series "
            "lacks columns: " + ", ".join(missing_weather_columns)
        )

    required_column_checks = {
        "weather_measurements": {"temp_air_original"},
        "weather_temperature_clusters": {"temperature_series_id"},
        "yearly_analysis": {
            "weather_temperature_series_id",
            "weather_cluster_k",
            "weather_cluster_start_time",
            "weather_cluster_end_time",
        },
        "yearly_analysis_weather_revisions": {
            "id",
            "yearly_analysis_id",
            "previous_features",
            "previous_prediction_run_ids",
            "new_prediction_run_ids",
            "previous_cluster_k",
            "previous_cluster_start_time",
            "previous_cluster_end_time",
            "status",
        },
        "prediction_runs": _PREDICTION_STACK_COLUMNS,
        "trip_predictions": _TRIP_COMPONENT_COLUMNS,
    }
    for table_name, required_columns in required_column_checks.items():
        result = await session.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = :table_name
                """
            ),
            {"table_name": table_name},
        )
        missing = sorted(required_columns - set(result.scalars().all()))
        if missing:
            raise RuntimeError(
                f"required schema migrations are missing or incomplete; {table_name} lacks "
                + ", ".join(missing)
            )

    prediction_constraint_result = await session.execute(
        text(
            """
            SELECT conname
            FROM pg_constraint
            WHERE conrelid = 'public.prediction_runs'::regclass
            """
        )
    )
    if _PREDICTION_STACK_CONSTRAINT not in set(
        prediction_constraint_result.scalars().all()
    ):
        raise RuntimeError(
            "migration 008 is incomplete; prediction_runs lacks constraint "
            f"{_PREDICTION_STACK_CONSTRAINT}"
        )

    weather_index_result = await session.execute(
        text(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = 'public'
              AND tablename = 'weather_temperature_series'
            """
        )
    )
    if _HYBRID_WEATHER_INDEX not in set(weather_index_result.scalars().all()):
        raise RuntimeError(
            "migration 007 is incomplete; weather_temperature_series lacks index "
            f"{_HYBRID_WEATHER_INDEX}"
        )


async def elevation_job_health_metadata(session) -> dict[str, int | str | None]:
    """Summarise queue readiness against the configured aux provenance pins."""

    runtime = runtime_release_configuration()
    result = await session.execute(
        text(
            """
            SELECT
                count(*) AS total,
                count(*) FILTER (WHERE status = 'succeeded') AS succeeded,
                count(*) FILTER (WHERE status = 'failed') AS failed,
                count(*) FILTER (WHERE status IN ('pending', 'processing')) AS not_ready,
                count(*) FILTER (
                    WHERE status = 'succeeded'
                      AND (
                        (CAST(:algorithm AS text) IS NOT NULL
                         AND algorithm_version IS DISTINCT FROM CAST(:algorithm AS text))
                        OR
                        (CAST(:roads_release AS text) IS NOT NULL
                         AND roads_release IS DISTINCT FROM CAST(:roads_release AS text))
                      )
                ) AS incompatible
            FROM elevation_profile_jobs
            """
        ),
        {
            "algorithm": runtime.aux_algorithm,
            "roads_release": runtime.aux_roads_release,
        },
    )
    row = result.fetchone()
    if row is None:
        raise RuntimeError("Unable to read elevation profile job health")
    return {
        "total": int(row[0]),
        "succeeded": int(row[1]),
        "failed": int(row[2]),
        "not_ready": int(row[3]),
        "incompatible": int(row[4]),
        "required_algorithm": runtime.aux_algorithm,
        "required_roads_release": runtime.aux_roads_release,
    }


async def probe_elevation_profiles_store() -> str:
    """Contact MinIO for both release and compatibility-mode deployments."""
    release_id = configured_release()
    if release_id is not None:
        await asyncio.to_thread(probe_configured_release_immutable)
        return (
            f"MinIO GTFS elevation release '{release_id}' is active and complete "
            f"in bucket '{gtfs_elevation_profiles_bucket()}'"
        )

    bucket = elevation_profiles_bucket()
    client = create_minio_client()
    exists = await asyncio.to_thread(client.bucket_exists, bucket)
    if not exists:
        raise RuntimeError(f"MinIO bucket {bucket!r} does not exist")
    return f"Legacy MinIO elevation namespace is reachable in bucket '{bucket}'"

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
    runtime_release = runtime_release_configuration()
    profile_status = await probe_elevation_profiles_store()
    release_id = configured_release()
    async with AsyncSessionLocal() as release_session:
        await release_session.execute(text("SELECT 1"))
        await validate_elevation_jobs_schema(release_session)
        if release_id is not None:
            release_manifest = validate_configured_release()
            validate_production_profile_contract(release_manifest)
            await validate_release_covers_database(release_session, release_manifest)
            await asyncio.to_thread(
                validate_configured_model_release, release_manifest
            )
    if release_id is not None:
        logger.info(
            "Elevation profiles: gtfs_bucket=%s aux_bucket=%s release=%s "
            "model=%s (manifest validated)",
            gtfs_elevation_profiles_bucket(),
            elevation_profiles_bucket(),
            release_id,
            runtime_release.consumption_model_release,
        )
    else:
        logger.warning(
            "Elevation profiles: legacy root namespace active because "
            "ELEVATION_PROFILES_RELEASE is not configured"
        )
    logger.info("Startup dependency preflight passed: %s", profile_status)
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
        - Yearly Analysis: Annual analysis records, optionally linked to optimization runs.
          Endpoints include ``GET /api/v1/yearly-analysis/{id}/energy-summary`` (aggregate
          electric and diesel-heating energy from prediction runs), ``GET …/costs`` (mixed
          e-bus vs full-diesel comparator costs), and ``GET …/emissions`` (mixed e-bus vs
          full-diesel comparator emissions using ``config/emission_defaults.json`` factors).
        - Weather Integration: PVGIS TMY weather data
        - Elevation Profiles: SwissTopo elevation data integration
        
        ### Environmental Calculations (LCA)
        - Vehicle Catalogue: Browse Energie Schweiz LCA vehicle database
        - Impact Analysis: Life-cycle environmental impact (GWP, PM, NOx, …)
        - Vehicle Mass: Mass composition breakdown
        - Electricity Mixes: Swiss electricity mix data
        - Fuel Blends: Fuel blend composition data
        - Data Versions: Historical data version management
        
        ### Economic Evaluations
        - Investment Costs: Battery, bus body, charger, grid connection, diesel bus
        - Cost Annualisation: Capital Recovery Factor (CRF) calculations
        - Operating Expenses: Maintenance, energy (electric), fuel (diesel)
        - Full Comparison: Side-by-side electric vs diesel annual cost analysis
        
        ### External Services Integration
        - OSRM Routing: Driving distance calculations
        - SwissTopo: Elevation profile generation
        - PVGIS: Weather data for solar calculations
        - MinIO: File storage for elevation profiles
        - Energie Schweiz LCA API: Environmental impact data
        
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
app.include_router(environmental.router, prefix="/api/v1/environmental", tags=["Environmental Calculations"])
app.include_router(economic.router, prefix="/api/v1/economic", tags=["Economic Evaluations"])
app.include_router(yearly_analysis.router, prefix="/api/v1/yearly-analysis", tags=["Yearly Analysis"])

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


@app.get(
    "/health",
    response_model=HealthCheckResponse,
    tags=["Health"],
    responses={503: {"model": HealthCheckResponse}},
)
async def health_check(response: Response):
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
            await validate_elevation_jobs_schema(session)
            elevation_jobs = await elevation_job_health_metadata(session)
            response_time = (time.time() - start_time) * 1000
            services["database"] = ServiceStatus(
                status="healthy",
                message="Database connection and migrations 005/006/007/008 are ready",
                response_time_ms=round(response_time, 2),
                last_checked=timestamp,
                metadata={"elevation_profile_jobs": elevation_jobs},
            )
            break
    except Exception as e:
        services["database"] = ServiceStatus(
            status="unhealthy",
            message=f"Database connection failed: {str(e)}",
            last_checked=timestamp
        )
        overall_status = "unhealthy"

    try:
        start_time = time.time()
        await asyncio.to_thread(probe_configured_model_immutable)
        services["consumption_model"] = ServiceStatus(
            status="healthy",
            message="Configured consumption model identity is stable",
            response_time_ms=round((time.time() - start_time) * 1000, 2),
            last_checked=timestamp,
            metadata=model_release_runtime_metadata(),
        )
    except Exception as e:
        services["consumption_model"] = ServiceStatus(
            status="unhealthy",
            message=f"Consumption model validation failed: {str(e)}",
            last_checked=timestamp,
        )
        overall_status = "unhealthy"

    # Probe MinIO on every readiness request. In compatibility mode this still
    # contacts the configured bucket instead of declaring the legacy namespace
    # healthy without checking its storage dependency.
    try:
        start_time = time.time()
        minio_message = await probe_elevation_profiles_store()
        services["elevation_profiles"] = ServiceStatus(
            status="healthy",
            message=minio_message,
            response_time_ms=round((time.time() - start_time) * 1000, 2),
            last_checked=timestamp,
            metadata=elevation_release_runtime_metadata(),
        )
    except Exception as e:
        services["elevation_profiles"] = ServiceStatus(
            status="unhealthy",
            message=f"Elevation release validation failed: {str(e)}",
            last_checked=timestamp,
        )
        overall_status = "unhealthy"
    
    # Check external services (optional - can be extended)
    # For now, we'll just check if the application is running
    try:
        runtime_metadata = runtime_release_configuration().metadata()
        services["application"] = ServiceStatus(
            status="healthy",
            message="Application is running with a coherent release configuration",
            last_checked=timestamp,
            metadata=runtime_metadata,
        )
    except Exception as e:
        services["application"] = ServiceStatus(
            status="unhealthy",
            message=f"Runtime release configuration failed: {str(e)}",
            last_checked=timestamp,
        )
        overall_status = "unhealthy"
    
    # Determine overall status
    if any(service.status == "unhealthy" for service in services.values()):
        overall_status = "unhealthy"
    elif any(service.status == "degraded" for service in services.values()):
        overall_status = "degraded"
    
    if overall_status == "unhealthy":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthCheckResponse(
        status=overall_status,
        timestamp=timestamp,
        version=settings.app_version,
        services=services,
        uptime_seconds=round(uptime_seconds, 2)
    )

if __name__ == "__main__":
    uvicorn.run(app, host=settings.host, port=settings.port)
