"""Unit coverage for the shared bus-model to LCA size resolver."""

from __future__ import annotations

import math

import pytest

from app.services.lca_size import (
    LcaSizeResolution,
    bus_length_to_lca_size_prefix,
    extract_lca_size_prefix,
    resolve_lca_size_prefix,
)


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("9m", "9m"),
        ("9m Midi", "9m"),
        ("13m Standard", "13m"),
        ("18m Articulated", "18m"),
        (" 18m Articulated ", "18m"),
    ],
)
def test_explicit_size_labels_resolve_to_supported_prefixes(label, expected):
    assert extract_lca_size_prefix(label) == expected


@pytest.mark.parametrize(
    "label",
    [None, "", "   ", "None", "N/A", "12m", "18M", 18, True, object()],
)
def test_unsupported_size_labels_remain_unresolved(label):
    assert extract_lca_size_prefix(label) is None


@pytest.mark.parametrize(
    ("length", "expected"),
    [
        (0.1, "9m"),
        (9, "9m"),
        (10, "9m"),
        (10.0001, "13m"),
        (12, "13m"),
        (14, "13m"),
        (14.0001, "18m"),
        (18, "18m"),
        (24, "18m"),
        ("18", "18m"),
    ],
)
def test_positive_bus_lengths_map_at_the_documented_boundaries(length, expected):
    assert bus_length_to_lca_size_prefix(length) == expected


@pytest.mark.parametrize(
    "length",
    [None, True, False, 0, -1, "", "not-a-length", math.nan, math.inf, -math.inf],
)
def test_invalid_bus_lengths_never_create_an_lca_category(length):
    assert bus_length_to_lca_size_prefix(length) is None


def test_explicit_size_is_authoritative_when_length_disagrees():
    assert resolve_lca_size_prefix(
        {"size": "13m Standard", "bus_length_m": 18},
    ) == LcaSizeResolution(prefix="13m", source="size")


@pytest.mark.parametrize("size", [None, "", "N/A", "12m Unsupported"])
def test_length_is_used_only_when_explicit_size_is_not_usable(size):
    assert resolve_lca_size_prefix(
        {"size": size, "bus_length_m": 18},
    ) == LcaSizeResolution(prefix="18m", source="bus_length_m")


@pytest.mark.parametrize("specs", [None, [], "18m", {}, {"bus_length_m": True}])
def test_missing_or_non_mapping_specs_remain_unresolved(specs):
    assert resolve_lca_size_prefix(specs) is None
