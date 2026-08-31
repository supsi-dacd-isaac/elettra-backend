"""Unit coverage for the bus-model write contract.

These tests do not create or migrate database rows.  In particular, they
exercise the intentional split between permissive legacy reads and strict
create/update requests.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models import BusesModels, Users
from app.routers.user import create_bus_model, update_bus_model
from app.schemas.database import BusesModelsRead
from app.schemas.requests import (
    BusModelCreateRequest,
    BusModelPhysicalSpecs,
    BusModelUpdateRequest,
)


VALID_SPECS = {
    "bus_length_m": 12,
    "empty_weight_kg": 12_000,
    "battery_pack_size_kwh": 40,
    "battery_pack_weight_kg": 274,
    "min_battery_packs": 6,
    "max_battery_packs": 10,
    "max_passengers": 80,
}


class FakeBusModelSession:
    """Minimal AsyncSession substitute for endpoint contract tests."""

    def __init__(self, *, user_id, bus_model=None):
        self.user_id = user_id
        self.bus_model = bus_model
        self.commit_count = 0

    async def get(self, entity, entity_id):
        if entity is Users:
            return object() if entity_id == self.user_id else None
        if entity is BusesModels:
            if self.bus_model is not None and entity_id == self.bus_model.id:
                return self.bus_model
            return None
        raise AssertionError(f"unexpected entity lookup: {entity!r}")

    def add(self, value):
        value.id = uuid4()
        self.bus_model = value

    async def commit(self):
        self.commit_count += 1

    async def refresh(self, _value):
        return None


def test_normal_json_integers_are_accepted_for_float_fields():
    parsed = BusModelPhysicalSpecs.model_validate(VALID_SPECS)

    assert parsed.bus_length_m == 12.0
    assert parsed.empty_weight_kg == 12_000.0
    assert parsed.battery_pack_size_kwh == 40.0
    assert parsed.battery_pack_weight_kg == 274.0


@pytest.mark.parametrize("missing_field", sorted(VALID_SPECS))
def test_all_physical_fields_are_required(missing_field):
    payload = deepcopy(VALID_SPECS)
    payload.pop(missing_field)

    with pytest.raises(ValidationError):
        BusModelPhysicalSpecs.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("bus_length_m", 0),
        ("bus_length_m", -1),
        ("bus_length_m", float("nan")),
        ("bus_length_m", float("inf")),
        ("bus_length_m", True),
        ("bus_length_m", "12"),
        ("empty_weight_kg", 0),
        ("battery_pack_size_kwh", -1),
        ("battery_pack_weight_kg", float("-inf")),
        ("min_battery_packs", 0),
        ("min_battery_packs", 1.5),
        ("min_battery_packs", True),
        ("max_battery_packs", "10"),
        ("max_passengers", 0),
        ("max_passengers", 80.0),
    ],
)
def test_invalid_numeric_values_are_rejected(field, invalid_value):
    payload = {**VALID_SPECS, field: invalid_value}

    with pytest.raises(ValidationError):
        BusModelPhysicalSpecs.model_validate(payload)


def test_pack_bounds_are_ordered():
    with pytest.raises(ValidationError, match="min_battery_packs"):
        BusModelPhysicalSpecs.model_validate(
            {**VALID_SPECS, "min_battery_packs": 11, "max_battery_packs": 10}
        )


def test_additional_specs_are_preserved():
    payload = {
        **VALID_SPECS,
        "max_charging_power_kw": 450,
        "lca": {"battery_chemistry": "NMC"},
        "auxiliary_consumption_kw": {"default": {"consumption_kw": [8, 9]}},
    }

    assert BusModelPhysicalSpecs.model_validate(payload).model_dump() == payload


def test_read_contract_remains_permissive_for_incomplete_legacy_specs():
    legacy = BusesModelsRead.model_validate(
        {
            "id": uuid4(),
            "name": "Legacy",
            "user_id": uuid4(),
            "specs": {"battery_kwh": 300},
            "manufacturer": None,
            "description": None,
        }
    )

    assert legacy.specs == {"battery_kwh": 300}


@pytest.mark.parametrize("specs", [None, [], {}])
def test_create_rejects_null_non_object_and_incomplete_specs(specs):
    with pytest.raises(ValidationError):
        BusModelCreateRequest.model_validate(
            {"name": "Invalid", "user_id": uuid4(), "specs": specs}
        )


def test_update_distinguishes_omitted_specs_from_explicit_null():
    update = BusModelUpdateRequest.model_validate({"description": "metadata only"})
    assert "specs" not in update.model_fields_set
    assert "specs" not in update.model_dump(exclude_unset=True)

    with pytest.raises(ValidationError):
        BusModelUpdateRequest.model_validate({"specs": None})


def test_update_specs_json_schema_is_optional_non_nullable_without_default():
    schema = BusModelUpdateRequest.model_json_schema()
    specs_schema = schema["properties"]["specs"]

    assert "specs" not in schema.get("required", [])
    assert "default" not in specs_schema
    assert specs_schema == {"$ref": "#/$defs/BusModelPhysicalSpecs"}


@pytest.mark.parametrize("request_model", [BusModelCreateRequest, BusModelUpdateRequest])
@pytest.mark.parametrize("invalid_name", [None, "", "   ", True, 123])
def test_create_and_update_reject_invalid_names(request_model, invalid_name):
    payload = {"name": invalid_name}
    if request_model is BusModelCreateRequest:
        payload.update({"user_id": uuid4(), "specs": VALID_SPECS})

    with pytest.raises(ValidationError):
        request_model.model_validate(payload)


@pytest.mark.parametrize("request_model", [BusModelCreateRequest, BusModelUpdateRequest])
def test_create_and_update_trim_names(request_model):
    payload = {"name": "  Valid model  "}
    if request_model is BusModelCreateRequest:
        payload.update({"user_id": uuid4(), "specs": VALID_SPECS})

    assert request_model.model_validate(payload).name == "Valid model"


def test_create_endpoint_serializes_validated_specs_and_extras():
    user_id = uuid4()
    session = FakeBusModelSession(user_id=user_id)
    payload = BusModelCreateRequest.model_validate(
        {
            "name": "Valid model",
            "user_id": user_id,
            "specs": {**VALID_SPECS, "purchase_cost_chf": 640_000},
        }
    )

    created = asyncio.run(create_bus_model(payload, db=session, current_user=object()))

    assert created.specs == {**VALID_SPECS, "purchase_cost_chf": 640_000}
    assert session.commit_count == 1


def test_metadata_only_update_keeps_incomplete_legacy_specs():
    user_id = uuid4()
    legacy = BusesModels(
        id=uuid4(),
        name="Legacy",
        user_id=user_id,
        specs={"battery_kwh": 300},
    )
    session = FakeBusModelSession(user_id=user_id, bus_model=legacy)

    updated = asyncio.run(
        update_bus_model(
            legacy.id,
            BusModelUpdateRequest.model_validate(
                {"name": "Legacy renamed", "description": "metadata edit"}
            ),
            db=session,
            current_user=object(),
        )
    )

    assert updated.name == "Legacy renamed"
    assert updated.description == "metadata edit"
    assert updated.specs == {"battery_kwh": 300}
    assert session.commit_count == 1


def test_invalid_specs_update_does_not_reach_or_modify_database_record():
    user_id = uuid4()
    legacy_specs = {"battery_kwh": 300}
    legacy = BusesModels(
        id=uuid4(),
        name="Legacy",
        user_id=user_id,
        specs=deepcopy(legacy_specs),
    )
    session = FakeBusModelSession(user_id=user_id, bus_model=legacy)

    with pytest.raises(ValidationError):
        BusModelUpdateRequest.model_validate(
            {"name": "Must not be applied", "specs": {"bus_length_m": 12}}
        )

    assert legacy.name == "Legacy"
    assert legacy.specs == legacy_specs
    assert session.commit_count == 0


@pytest.mark.parametrize("invalid_name", [None, "", "   "])
def test_invalid_name_update_does_not_reach_or_modify_database_record(invalid_name):
    user_id = uuid4()
    legacy_specs = {"battery_kwh": 300}
    legacy = BusesModels(
        id=uuid4(),
        name="Legacy",
        user_id=user_id,
        specs=deepcopy(legacy_specs),
    )
    session = FakeBusModelSession(user_id=user_id, bus_model=legacy)

    with pytest.raises(ValidationError):
        BusModelUpdateRequest.model_validate({"name": invalid_name})

    assert legacy.name == "Legacy"
    assert legacy.specs == legacy_specs
    assert session.commit_count == 0


def test_complete_specs_update_replaces_legacy_object():
    user_id = uuid4()
    legacy = BusesModels(
        id=uuid4(),
        name="Legacy",
        user_id=user_id,
        specs={"battery_kwh": 300},
    )
    session = FakeBusModelSession(user_id=user_id, bus_model=legacy)
    replacement = {**VALID_SPECS, "purchase_cost_chf": 610_000}

    updated = asyncio.run(
        update_bus_model(
            legacy.id,
            BusModelUpdateRequest.model_validate({"specs": replacement}),
            db=session,
            current_user=object(),
        )
    )

    assert updated.specs == replacement
    assert session.commit_count == 1
