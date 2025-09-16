# Health check schemas
from __future__ import annotations
from datetime import datetime
from typing import Optional, Dict, Any
from pydantic import BaseModel


class ServiceStatus(BaseModel):
    """Status of an individual service"""
    status: str  # "healthy", "unhealthy", "degraded"
    message: Optional[str] = None
    response_time_ms: Optional[float] = None
    last_checked: datetime


class HealthCheckResponse(BaseModel):
    """Overall health check response"""
    status: str  # "healthy", "unhealthy", "degraded"
    timestamp: datetime
    version: str
    services: Dict[str, ServiceStatus]
    uptime_seconds: Optional[float] = None
