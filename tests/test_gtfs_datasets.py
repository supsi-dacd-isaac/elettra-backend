from datetime import date
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.schemas.gtfs_dataset import GtfsDatasetRead, GtfsDatasetReference
from app.services.gtfs_datasets import (
    list_datasets, datasets_for_shifts, validate_shift_dataset,
    capture_provenance, inherit_provenance,
)


def edition(year=2026, file_date="2025-09-27"):
    return GtfsDatasetRead(gtfs_year=year, gtfs_file_date=file_date,
                           valid_from="2025-12-14", valid_to="2026-12-12")


def test_legacy_client_can_save_single_edition_and_auxiliary_only_blocks():
    validate_shift_dataset([edition()])
    validate_shift_dataset([])
    validate_shift_dataset([edition()], previous=[edition()])


@pytest.mark.parametrize("new,requested,previous", [
    ([edition(), edition(2027)], None, None),
    ([edition(), edition(2026, "2026-10-01")], None, None),
    ([edition(2027)], None, [edition()]),
    ([], None, [edition()]),
    ([edition()], edition(2027), None),
    ([], edition(), None),
    ([edition()], None, [edition(), edition(2027)]),
])
def test_rejects_mixing_retargeting_or_clearing_gtfs(new, requested, previous):
    with pytest.raises(HTTPException) as exc:
        validate_shift_dataset(new, requested, previous)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_catalog_is_company_scoped_and_only_aggregates_gtfs_calendars():
    company = uuid4()
    db = AsyncMock()
    db.execute.return_value = [(2027, date(2026, 11, 4), date(2026, 12, 13), date(2027, 12, 11))]
    result = await list_datasets(db, company)
    assert result[0].valid_from == date(2026, 12, 13)
    assert result[0].valid_to == date(2027, 12, 11)
    query = db.execute.call_args.args[0].compile(dialect=postgresql.dialect())
    assert company in query.params.values()
    assert "gtfs" in query.params.values()
    assert "DISTINCT" in str(query)
    assert "gtfs_year DESC" in str(query)
    assert "gtfs_file_date DESC" in str(query)


@pytest.mark.asyncio
async def test_shift_batch_keeps_empty_and_historical_mixed_without_n_plus_one():
    a, b, empty, company = uuid4(), uuid4(), uuid4(), uuid4()
    db = AsyncMock()
    db.execute.side_effect = [
        [(a, 2026, date(2025, 9, 27)), (b, 2026, date(2025, 9, 27)), (b, 2027, date(2026, 11, 4))],
        [(2027, date(2026, 11, 4), date(2026, 12, 13), date(2027, 12, 11)),
         (2026, date(2025, 9, 27), date(2025, 12, 14), date(2026, 12, 12))],
    ]
    data = await datasets_for_shifts(db, [a, b, empty], company)
    assert [x.gtfs_year for x in data[a]] == [2026]
    assert [x.gtfs_year for x in data[b]] == [2027, 2026]
    assert data[empty] == []
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_empty_shift_page_never_scans_catalog():
    db = AsyncMock()
    assert await datasets_for_shifts(db, [], uuid4()) == {}
    db.execute.assert_not_awaited()


def test_analysis_provenance_ignores_client_and_is_detached_from_source():
    source = {"version": 1, "source": "recorded", "shifts": {"s": [edition().model_dump(mode="json")]}}
    features = {"other": 5, "gtfs_provenance": {"forged": True}}
    saved = inherit_provenance(features, source)
    source["shifts"]["s"].clear()
    assert saved["gtfs_provenance"]["shifts"]["s"][0]["gtfs_year"] == 2026
    assert saved["other"] == 5
    assert "gtfs_provenance" not in inherit_provenance(features, None)
    assert features["gtfs_provenance"] == {"forged": True}


def test_dataset_input_rejects_client_validity_and_partial_identity():
    with pytest.raises(ValueError):
        GtfsDatasetReference(gtfs_year=2027)
    with pytest.raises(ValueError):
        GtfsDatasetReference(gtfs_year=2027, gtfs_file_date="2026-11-04", valid_from="2020-01-01")
