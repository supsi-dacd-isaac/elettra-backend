"""Reusable pagination primitives for list endpoints.

This module exposes:

- ``PaginatedResponse[T]`` — generic envelope returned by every paginated
  list endpoint. Always contains ``items`` plus ``total``, ``skip``,
  ``limit``, ``count``, ``has_next`` and ``has_previous``.
- ``PaginationParams`` — FastAPI dependency that validates the query
  parameters ``skip`` and ``limit`` consistently across endpoints. Allowed
  page sizes are restricted to ``20``, ``50`` and ``100`` (default ``20``).
- ``ALLOWED_PAGE_SIZES`` — the canonical tuple of accepted limits.
- ``build_paginated_response`` — helper that assembles the envelope from a
  list of items, the database-side total and the pagination parameters.

The query/count work itself is not done here on purpose: each router is
expected to perform its own ``LIMIT/OFFSET`` and ``COUNT(*)`` queries so
that filters/joins remain explicit.
"""

from __future__ import annotations

from typing import Generic, Sequence, TypeVar

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field

ALLOWED_PAGE_SIZES: tuple[int, ...] = (20, 50, 100)
DEFAULT_PAGE_SIZE: int = 20

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    """Standard envelope returned by paginated list endpoints."""

    items: list[T] = Field(
        default_factory=list,
        description="Page of items for the requested ``skip``/``limit`` window.",
    )
    total: int = Field(
        ...,
        ge=0,
        description="Total number of rows that match the filtered query (not just the page).",
        examples=[137],
    )
    skip: int = Field(
        ...,
        ge=0,
        description="Number of rows skipped before this page (offset).",
        examples=[0],
    )
    limit: int = Field(
        ...,
        ge=1,
        description=(
            "Maximum number of rows returned for this page. "
            "Restricted to one of 20, 50 or 100."
        ),
        examples=[20],
    )
    count: int = Field(
        ...,
        ge=0,
        description="Number of rows actually returned in ``items`` (``<= limit``).",
        examples=[20],
    )
    has_next: bool = Field(
        ...,
        description="``True`` when more rows are available beyond this page.",
        examples=[True],
    )
    has_previous: bool = Field(
        ...,
        description="``True`` when a previous page exists (``skip > 0``).",
        examples=[False],
    )

    model_config = ConfigDict(from_attributes=True)


class PaginationParams(BaseModel):
    """Validated ``skip`` / ``limit`` query parameters.

    Use as a FastAPI dependency: ``pagination: PaginationParams = Depends()``.
    """

    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=DEFAULT_PAGE_SIZE)

    model_config = ConfigDict(extra="forbid")

    def __init__(
        self,
        skip: int = Query(
            0,
            ge=0,
            description="Number of rows to skip (offset). Must be ``>= 0``.",
        ),
        limit: int = Query(
            DEFAULT_PAGE_SIZE,
            description=(
                "Maximum number of rows to return. "
                f"Allowed values: {', '.join(str(s) for s in ALLOWED_PAGE_SIZES)}."
            ),
            examples=list(ALLOWED_PAGE_SIZES),
        ),
    ) -> None:
        if limit not in ALLOWED_PAGE_SIZES:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=422,
                detail=(
                    f"Invalid limit={limit}. Allowed values: "
                    f"{list(ALLOWED_PAGE_SIZES)}."
                ),
            )
        super().__init__(skip=skip, limit=limit)


def build_paginated_response(
    items: Sequence[T],
    total: int,
    skip: int,
    limit: int,
) -> PaginatedResponse[T]:
    """Assemble a :class:`PaginatedResponse` from query results.

    ``items`` should already correspond to the requested ``skip``/``limit``
    window (i.e. fetched with ``OFFSET skip LIMIT limit`` at the database
    level). ``total`` should come from a separate ``COUNT(*)`` query that
    applies the same filters as the items query, but no pagination.
    """

    items_list = list(items)
    count = len(items_list)
    has_previous = skip > 0
    has_next = (skip + count) < total
    return PaginatedResponse[T](
        items=items_list,
        total=int(total),
        skip=int(skip),
        limit=int(limit),
        count=count,
        has_next=has_next,
        has_previous=has_previous,
    )


__all__ = [
    "ALLOWED_PAGE_SIZES",
    "DEFAULT_PAGE_SIZE",
    "PaginatedResponse",
    "PaginationParams",
    "build_paginated_response",
]
