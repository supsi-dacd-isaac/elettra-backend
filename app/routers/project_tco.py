"""Read-only project-horizon previews for the annual-analysis dashboard."""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, StringConstraints, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.ownership import get_owned_yearly_analysis
from app.database import get_async_session
from app.models import Users
from app.services.project_tco import project_design


class ProjectTcoRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        handler = super().get_route_handler()

        async def validate_request(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as exc:
                # Invalid input can contain nonfinite numbers that JSON cannot encode.
                detail = [
                    {key: error[key] for key in ("loc", "msg", "type")}
                    for error in exc.errors()
                ]
                return JSONResponse(status_code=422, content={"detail": detail})

        return validate_request


router = APIRouter(route_class=ProjectTcoRoute)

LineKey = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r"\S")]
ScopeId = Annotated[str, StringConstraints(min_length=1, max_length=256)]
Nonnegative = Annotated[FiniteFloat, Field(ge=0)]


class PreviewInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class ProjectAssetInput(PreviewInput):
    key: LineKey
    initial: Nonnegative
    life: Annotated[FiniteFloat, Field(ge=1)]


class ProjectOperatingInput(PreviewInput):
    key: LineKey
    annual: Nonnegative


class ProjectDesignInput(PreviewInput):
    assets: list[ProjectAssetInput] = Field(max_length=64)
    operating: list[ProjectOperatingInput] = Field(max_length=64)


class ProjectScopeInput(PreviewInput):
    id: ScopeId
    km: Nonnegative
    electric: ProjectDesignInput | None = None
    diesel: ProjectDesignInput | None = None


class ProjectTcoInput(PreviewInput):
    horizon: int = Field(ge=1, le=60)
    rate: Annotated[FiniteFloat, Field(ge=0, le=1)]
    scopes: list[ProjectScopeInput] = Field(max_length=2048)

    @model_validator(mode="after")
    def validate_scopes(self) -> ProjectTcoInput:
        if len({scope.id for scope in self.scopes}) != len(self.scopes):
            raise ValueError("Scope IDs must be unique")
        projected_lines = sum(
            len(design.operating) + sum(math.ceil(self.horizon / asset.life) for asset in design.assets)
            for scope in self.scopes
            for design in (scope.electric, scope.diesel)
            if design is not None
        )
        if projected_lines > 100_000:
            raise ValueError("The request exceeds 100,000 projected cash-flow lines")
        return self


class ProjectReplacement(BaseModel):
    year: float
    cost: float
    discounted: float


class ProjectAssetResult(BaseModel):
    key: str
    life: float
    initial: float
    replacements: list[ProjectReplacement]
    residual: float
    discountedReplacements: float
    discountedResidual: float
    presentValue: float


class ProjectDesignResult(BaseModel):
    components: list[ProjectAssetResult]
    operating: list[ProjectOperatingInput]
    initial: float
    annualOpex: float
    presentValue: float
    annuityFactor: float
    annualEquivalent: float
    perKm: float | None
    residual: float
    discountedResidual: float
    discountedReplacements: float


class ProjectScopeResult(BaseModel):
    id: str
    electric: ProjectDesignResult | None
    diesel: ProjectDesignResult | None


class ProjectTcoResult(BaseModel):
    scopes: list[ProjectScopeResult]


def _project(
    design: ProjectDesignInput | None, *, km: float, horizon: int, rate: float
) -> dict | None:
    if design is None:
        return None
    try:
        return project_design(
            assets=[(asset.key, asset.initial, asset.life) for asset in design.assets],
            operating=[(cost.key, cost.annual) for cost in design.operating],
            annual_km=km,
            horizon=horizon,
            rate=rate,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{yearly_analysis_id}/costs/project", response_model=ProjectTcoResult)
async def preview_project_tco(
    yearly_analysis_id: UUID,
    payload: ProjectTcoInput,
    db: AsyncSession = Depends(get_async_session),
    current_user: Users = Depends(get_current_user),
) -> dict:
    await get_owned_yearly_analysis(db, yearly_analysis_id, current_user.id)
    return {
        "scopes": [
            {
                "id": scope.id,
                "electric": _project(scope.electric, km=scope.km, horizon=payload.horizon, rate=payload.rate),
                "diesel": _project(scope.diesel, km=scope.km, horizon=payload.horizon, rate=payload.rate),
            }
            for scope in payload.scopes
        ],
    }
