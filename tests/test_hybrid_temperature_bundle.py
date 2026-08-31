from __future__ import annotations

from pathlib import Path

import pytest

from app.services.hybrid_temperature import (
    HYBRID_PROVIDER,
    OPENMETEO_MODEL,
    PROCESSING_VERSION,
)
from scripts.backfill_hybrid_temperature import (
    BUNDLE_SCHEMA_VERSION,
    _bundle_checksum,
    _read_bundle,
    _write_bundle,
)


def _manifest() -> dict:
    bundle = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "provider": HYBRID_PROVIDER,
        "processing_version": PROCESSING_VERSION,
        "openmeteo_model": OPENMETEO_MODEL,
        "inventory_count": 0,
        "entries": [],
        "failures": [],
    }
    bundle["bundle_checksum"] = _bundle_checksum(bundle)
    return bundle


def test_bundle_manifest_checksum_is_verified(tmp_path: Path):
    path = tmp_path / "bundle.json.gz"
    bundle = _manifest()
    _write_bundle(path, bundle)
    assert _read_bundle(path)["bundle_checksum"] == bundle["bundle_checksum"]

    bundle["provider"] = "tampered"
    _write_bundle(path, bundle)
    with pytest.raises(ValueError, match="manifest checksum"):
        _read_bundle(path)


def test_bundle_with_planning_failures_cannot_be_applied(tmp_path: Path):
    path = tmp_path / "bundle.json.gz"
    bundle = _manifest()
    bundle["failures"] = [{"coordinate": "46.8,7.1", "error": "upstream"}]
    bundle["bundle_checksum"] = _bundle_checksum(
        {key: value for key, value in bundle.items() if key != "bundle_checksum"}
    )
    _write_bundle(path, bundle)
    with pytest.raises(ValueError, match="planning failures"):
        _read_bundle(path)
