"""Compile a vehicle programme into ordered GTFS and empty movements."""
from dataclasses import dataclass
import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.schemas.gtfs_dataset import GtfsDatasetReference
from app.schemas.service_weekday import ServiceWeekday

Time = Annotated[str, StringConstraints(pattern=r"^[0-9]{2}:[0-5][0-9](:[0-5][0-9])?$")]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Service(Input):
    kind: Literal["service"]
    trip_id: UUID
    transfer_departure: Time | None = None
    transfer_arrival: Time | None = None
    force_transfer: bool = False


class Stay(Input):
    kind: Literal["stay"]
    depot_id: UUID
    transfer_departure: Time
    arrival: Time
    departure: Time


class Return(Input):
    kind: Literal["return"]
    transfer_departure: Time
    arrival: Time


class Programme(Input):
    version: Literal[1]
    weekday: ServiceWeekday
    depot_id: UUID
    start_time: Time
    steps: list[Annotated[Service | Stay | Return, Field(discriminator="kind")]] = Field(min_length=2, max_length=500)


class ProgrammeRequest(Input):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    bus_id: UUID
    gtfs_dataset: GtfsDatasetReference
    programme: Programme
    request_id: UUID | None = None
    copy_from: UUID | None = None
    revision: int | None = Field(default=None, ge=1)


def seconds(value):
    if not value or not re.fullmatch(r"[0-9]{1,2}:[0-5][0-9](:[0-5][0-9])?", value):
        raise ValueError("A movement has an invalid service-day time")
    parts = [int(n) for n in value.split(":")]
    return 3600 * parts[0] + 60 * parts[1] + (parts[2] if len(parts) == 3 else 0)


def clock(value):
    amount = seconds(value)
    return f"{amount // 3600:02d}:{amount // 60 % 60:02d}:{amount % 60:02d}"


@dataclass(frozen=True)
class Movement:
    status: str
    origin: UUID
    destination: UUID
    departure: str
    arrival: str
    route_id: UUID


def compile_programme(programme, services, places):
    """Use agency stop UUIDs and timetable seconds, even after midnight."""
    selected = [step.trip_id for step in programme.steps if isinstance(step, Service)]
    if not selected or len(set(selected)) != len(selected):
        raise ValueError("Choose at least one scheduled trip, and choose each trip once")
    if any(trip not in services for trip in selected):
        raise ValueError("A scheduled trip is unavailable on this weekday or timetable edition")
    if not isinstance(programme.steps[-1], Return) or any(isinstance(x, Return) for x in programme.steps[:-1]):
        raise ValueError("Close the programme at the original depot")
    base = places[programme.depot_id]
    current, parent, available = base, None, programme.start_time
    at_special_stop = True
    # Empty movements belong to the next scheduled service, or the last one on the return home.
    service_routes = iter(services[trip_id]["route_id"] for trip_id in selected)
    route_id = next(service_routes)
    result = []

    def connect(stop, dep, arr, status, route):
        if seconds(dep) < seconds(available) or seconds(arr) < seconds(dep):
            raise ValueError("The transfer overlaps another movement")
        if index == 0 and seconds(dep) != seconds(programme.start_time):
            raise ValueError("The first departure must match the depot departure")
        if current != stop:
            if seconds(dep) == seconds(arr):
                raise ValueError("A movement between different stops needs positive travel time")
            result.append(Movement(status, current, stop, clock(dep), clock(arr), route))
        elif seconds(arr) != seconds(dep):
            raise ValueError("Use a stay for time spent at the same stop")

    for index, step in enumerate(programme.steps):
        if isinstance(step, Service):
            t = services[step.trip_id]
            start, end = t["departure_time"], t["arrival_time"]
            same = current == t["from_stop_id"] or (parent and parent == t["from_parent"])
            if seconds(start) < seconds(available) or seconds(end) < seconds(start):
                raise ValueError("Scheduled trips overlap or run backwards")
            if not same or step.force_transfer:
                arr = step.transfer_arrival or t["first_arrival"] or start
                if seconds(arr) > seconds(start):
                    raise ValueError("Arrival at the terminus follows the published departure")
                connect(t["from_stop_id"], step.transfer_departure or available, arr,
                        "depot" if at_special_stop else "transfer", route_id)
            elif step.transfer_arrival or step.transfer_departure:
                # With direct continuity an optional operating arrival is a stay at the terminal.
                arr = step.transfer_arrival or available
                dep = step.transfer_departure or available
                if not seconds(available) <= seconds(dep) <= seconds(arr) <= seconds(start):
                    raise ValueError("Operating arrival falls outside the available interval")
            at_special_stop = False
            result.append(step.trip_id)
            current, parent, available = t["to_stop_id"], t["to_parent"], end
            route_id = next(service_routes, route_id)
        else:
            target = places[step.depot_id] if isinstance(step, Stay) else base
            connect(target, step.transfer_departure, step.arrival, "depot", route_id)
            if isinstance(step, Stay) and seconds(step.departure) < seconds(step.arrival):
                raise ValueError("The stay ends before the bus arrives")
            at_special_stop = True
            current, parent = target, None
            available = step.departure if isinstance(step, Stay) else step.arrival
    if len(result) > 500:
        raise ValueError("The programme exceeds the maximum of 500 trips")
    return result
