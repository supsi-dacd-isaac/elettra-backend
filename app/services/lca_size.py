"""Resolve bus-model metadata to the canonical Mobitool LCA size classes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from typing import Literal


LCA_SIZE_PREFIXES = ("9m", "13m", "18m")


@dataclass(frozen=True)
class LcaSizeResolution:
    """A resolved LCA size prefix and the bus-model field that supplied it."""

    prefix: str
    source: Literal["size", "bus_length_m"]


def extract_lca_size_prefix(model_size: object) -> str | None:
    """Extract a supported LCA prefix from a human-readable size label."""

    if not isinstance(model_size, str):
        return None
    normalized = model_size.strip()
    if not normalized or normalized in {"None", "N/A"}:
        return None
    return next(
        (prefix for prefix in LCA_SIZE_PREFIXES if normalized.startswith(prefix)),
        None,
    )


def bus_length_to_lca_size_prefix(bus_length_m: object) -> str | None:
    """Map a finite, positive bus length to the closest supported LCA class."""

    if bus_length_m is None or isinstance(bus_length_m, bool):
        return None
    try:
        length = float(bus_length_m)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(length) or length <= 0:
        return None
    if length <= 10:
        return "9m"
    if length <= 14:
        return "13m"
    return "18m"


def resolve_lca_size_prefix(
    specs: Mapping[str, object] | None,
) -> LcaSizeResolution | None:
    """Resolve ``specs.size`` first, then validated ``specs.bus_length_m``."""

    if not isinstance(specs, Mapping):
        return None

    size_prefix = extract_lca_size_prefix(specs.get("size"))
    if size_prefix is not None:
        return LcaSizeResolution(prefix=size_prefix, source="size")

    length_prefix = bus_length_to_lca_size_prefix(specs.get("bus_length_m"))
    if length_prefix is None:
        return None
    return LcaSizeResolution(prefix=length_prefix, source="bus_length_m")
