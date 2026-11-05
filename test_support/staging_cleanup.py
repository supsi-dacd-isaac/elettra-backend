"""Deterministic cleanup for integration-test graphs in the staging database."""

from __future__ import annotations

import asyncio
import uuid

import asyncpg

from app.core.config import get_cached_settings
from test_support.staging_targets import validate_test_targets


def cleanup_staging_simulation_graph(
    *,
    model_ids: list[str] | tuple[str, ...] = (),
    bus_ids: list[str] | tuple[str, ...] = (),
    shift_ids: list[str] | tuple[str, ...] = (),
) -> None:
    """Remove only the simulation graph rooted at exact test-created UUIDs.

    Public DELETE endpoints correctly reject referenced resources. Integration
    fixtures still need to clean up after failed or interrupted simulations, so
    this helper removes their private graph in dependency order. The target is
    validated before connecting and can only be the dedicated staging/CI test
    database accepted by :func:`validate_test_targets`.
    """

    settings = get_cached_settings()
    validate_test_targets(settings.database_url)
    dsn = settings.database_url
    if dsn.startswith("postgresql+asyncpg://"):
        dsn = "postgresql://" + dsn.split("://", 1)[1]

    model_uuids = [uuid.UUID(value) for value in model_ids]
    bus_uuids = [uuid.UUID(value) for value in bus_ids]
    shift_uuids = [uuid.UUID(value) for value in shift_ids]
    if not (model_uuids or bus_uuids or shift_uuids):
        return

    async def _cleanup() -> None:
        connection = await asyncpg.connect(dsn)
        try:
            async with connection.transaction():
                scoped_bus_rows = await connection.fetch(
                    """
                    SELECT id
                    FROM buses
                    WHERE id = ANY($1::uuid[])
                       OR bus_model_id = ANY($2::uuid[])
                    """,
                    bus_uuids,
                    model_uuids,
                )
                scoped_bus_ids = [row["id"] for row in scoped_bus_rows]

                scoped_shift_rows = await connection.fetch(
                    """
                    SELECT id
                    FROM shifts
                    WHERE id = ANY($1::uuid[])
                       OR bus_id = ANY($2::uuid[])
                    """,
                    shift_uuids,
                    scoped_bus_ids,
                )
                scoped_shift_ids = [row["id"] for row in scoped_shift_rows]
                shift_texts = [str(value) for value in scoped_shift_ids]
                model_texts = [str(value) for value in model_uuids]

                optimization_rows = await connection.fetch(
                    """
                    SELECT id
                    FROM optimization_runs
                    WHERE bus_model_id = ANY($1::uuid[])
                       OR input_params->>'bus_model_id' = ANY($2::text[])
                       OR input_params->'shift_ids' ?| $3::text[]
                    """,
                    model_uuids,
                    model_texts,
                    shift_texts,
                )
                optimization_ids = [row["id"] for row in optimization_rows]

                yearly_rows = await connection.fetch(
                    """
                    SELECT id
                    FROM yearly_analysis
                    WHERE optimization_run_id = ANY($1::uuid[])
                    UNION
                    SELECT yearly_analysis_id
                    FROM prediction_runs
                    WHERE yearly_analysis_id IS NOT NULL
                      AND (
                        shift_id = ANY($2::uuid[])
                        OR bus_model_id = ANY($3::uuid[])
                      )
                    """,
                    optimization_ids,
                    scoped_shift_ids,
                    model_uuids,
                )
                yearly_ids = [row["id"] for row in yearly_rows]

                await connection.execute(
                    "DELETE FROM yearly_analysis WHERE id = ANY($1::uuid[])",
                    yearly_ids,
                )
                await connection.execute(
                    "DELETE FROM optimization_runs WHERE id = ANY($1::uuid[])",
                    optimization_ids,
                )
                await connection.execute(
                    """
                    DELETE FROM prediction_runs
                    WHERE shift_id = ANY($1::uuid[])
                       OR bus_model_id = ANY($2::uuid[])
                    """,
                    scoped_shift_ids,
                    model_uuids,
                )
                await connection.execute(
                    "DELETE FROM shifts WHERE id = ANY($1::uuid[])",
                    scoped_shift_ids,
                )
                await connection.execute(
                    "DELETE FROM buses WHERE id = ANY($1::uuid[])",
                    scoped_bus_ids,
                )
                await connection.execute(
                    "DELETE FROM buses_models WHERE id = ANY($1::uuid[])",
                    model_uuids,
                )
        finally:
            await connection.close()

    asyncio.run(_cleanup())
