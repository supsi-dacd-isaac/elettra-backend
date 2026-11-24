"""Capture and replay the passenger policy for each occurrence of a shift."""

import numpy as np
from sqlalchemy import select

from app.models import GtfsTrips, Shifts, ShiftsStructures
from app.schemas.trip_status import TripStatus


TRIP_OCCUPANCY_KEY = "trip_occupancy"
PROGRAMME_OCCUPANCY_POLICY = "programme-v1-zero-depot-transfer-v1"
EMPTY_TRIP_STATUSES = {TripStatus.DEPOT.value, TripStatus.TRANSFER.value}


async def load_shift_trip_occupancy(db, shift_id, cache):
    """Resolve one snapshot per shift for a batch of new predictions."""
    key = (TRIP_OCCUPANCY_KEY, shift_id)
    if key not in cache:
        shift = await db.get(Shifts, shift_id)
        programme = getattr(shift, "programme", None)
        snapshot = None
        if programme and programme.get("version") == 1:
            statement = (
                select(ShiftsStructures.sequence_number, GtfsTrips.id, GtfsTrips.status)
                .join(GtfsTrips, GtfsTrips.id == ShiftsStructures.trip_id)
                .where(ShiftsStructures.shift_id == shift_id)
                .order_by(ShiftsStructures.sequence_number)
            )
            rows = (await db.execute(statement)).all()
            snapshot = {
                "policy_id": PROGRAMME_OCCUPANCY_POLICY,
                "occurrences": [
                    {"sequence_number": sequence, "trip_id": str(trip_id), "status": status}
                    for sequence, trip_id, status in rows
                ],
            }
        cache[key] = snapshot
    return cache[key]


def empty_trip_mask(snapshot, trip_statistics):
    """Align saved trip types by occurrence, preserving trips repeated in one shift."""
    if snapshot is None:
        return None
    if snapshot["policy_id"] != PROGRAMME_OCCUPANCY_POLICY:
        raise ValueError("Unknown saved trip occupancy policy")
    occurrences = snapshot["occurrences"]
    expected = [(row["sequence_number"], str(row["trip_id"])) for row in occurrences]
    actual = [(row["sequence_number"], str(row["trip_id"])) for row in trip_statistics]
    if expected != actual:
        raise ValueError("Saved trip occupancy does not match the prediction occurrences")
    return np.array([row["status"] in EMPTY_TRIP_STATUSES for row in occurrences], dtype=bool)


def trip_mass_kg(mass, row_count, empty_trips):
    if empty_trips is None:
        return np.full(row_count, mass.total_weight_kg)
    unoccupied_weight_kg = mass.empty_weight_kg + mass.battery_weight_kg
    return np.where(empty_trips, unoccupied_weight_kg, mass.total_weight_kg)
