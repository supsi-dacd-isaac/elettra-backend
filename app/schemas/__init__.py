# Schema exports - organized by category
from .database import *
from .auth import *
from .external_apis import *
from .responses import *
from .health import *
from .pagination import *

# Re-export everything for backward compatibility
__all__ = [
    # Database schemas (auto-generated)
    "GtfsAgenciesCreate", "GtfsAgenciesUpdate", "GtfsAgenciesRead",
    "GtfsCalendarCreate", "GtfsCalendarUpdate", "GtfsCalendarRead", 
    "GtfsStopsCreate", "GtfsStopsUpdate", "GtfsStopsRead",
    "ShiftsCreate", "ShiftsUpdate", "ShiftsRead",
    "WeatherMeasurementsCreate", "WeatherMeasurementsUpdate", "WeatherMeasurementsRead",
    "BusModelsCreate", "BusModelsUpdate", "BusModelsRead",
    "DepotsCreate", "DepotsUpdate", "DepotsRead",
    "GtfsRoutesCreate", "GtfsRoutesUpdate", "GtfsRoutesRead",
    "UsersCreate", "UsersUpdate", "UsersRead",
    "GtfsTripsCreate", "GtfsTripsUpdate", "GtfsTripsRead",
    "VariantsCreate", "VariantsUpdate", "VariantsRead",
    "GtfsStopsTimesCreate", "GtfsStopsTimesUpdate", "GtfsStopsTimesRead",
    "ShiftsStructuresCreate", "ShiftsStructuresUpdate", "ShiftsStructuresRead",
    "PredictionRunsCreate", "PredictionRunsRead",
    "TripPredictionsRead",
    "OptimizationRunsRead",
    "YearlyAnalysisCreate", "YearlyAnalysisUpdate", "YearlyAnalysisRead",
    
    # Auth schemas
    "UserLogin", "Token", "LogoutResponse", "RoleEnum", "UserRegister",
    
    # External API schemas
    "PvgisTmyRequest", "PvgisTmyResponse", "PvgisTmyMetadataResponse",
    "WeatherClusteringRequest", "WeatherClusteringResponse", "ClusterItem",
    "ElevationProfileResponse",
    
    # Custom response schemas
    "PredictionSubmitResponse", "OptimizationSubmitResponse",
    "GtfsStopsReadWithTimes", "VariantsReadWithRoute", "GtfsRoutesReadWithVariant",
    
    # Health check schemas
    "HealthCheckResponse", "ServiceStatus",
]
