from uuid import uuid4

from app.routers.agency import (
    NETWORK_PREVIEW_CACHE_LIMIT,
    _cache_network_preview,
    _coarsen_network_point,
    _get_cached_network_preview,
    _network_preview_cache,
    _sample_network_points,
)
from app.schemas.responses import AgencyNetworkPreviewRead


def test_network_preview_point_sampling_keeps_endpoints_and_bound():
    points = [(float(index), float(index * 2)) for index in range(100)]

    sampled = _sample_network_points(points, limit=12)

    assert len(sampled) == 12
    assert sampled[0] == points[0]
    assert sampled[-1] == points[-1]
    assert sampled == sorted(sampled)


def test_network_preview_coordinates_are_coarsened_to_street_level():
    assert _coarsen_network_point(46.495396, 7.67181) == (46.495, 7.672)


def test_network_preview_cache_expires_and_stays_bounded():
    _network_preview_cache.clear()
    previews = [
        AgencyNetworkPreviewRead(
            agency_id=uuid4(),
            agency_name=f"Agency {index}",
            route_count=0,
            lines=[],
        )
        for index in range(NETWORK_PREVIEW_CACHE_LIMIT + 1)
    ]
    for preview in previews:
        _cache_network_preview(preview, now=100.0)

    assert len(_network_preview_cache) == NETWORK_PREVIEW_CACHE_LIMIT
    assert _get_cached_network_preview(previews[0].agency_id, now=101.0) is None
    assert _get_cached_network_preview(previews[-1].agency_id, now=101.0) is previews[-1]
    assert _get_cached_network_preview(previews[-1].agency_id, now=3700.0) is None
    _network_preview_cache.clear()


def test_network_preview_is_public_and_bounded(client):
    agencies_response = client.get("/api/v1/agency/agencies/")
    assert agencies_response.status_code == 200
    agencies = agencies_response.json()
    if not agencies:
        return

    response = client.get(
        f"/api/v1/agency/agencies/{agencies[0]['id']}/network-preview"
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=3600"
    payload = response.json()
    assert set(payload) == {"agency_id", "agency_name", "route_count", "lines"}
    for line in payload["lines"]:
        assert set(line) == {"label", "color", "points"}
        assert 2 <= len(line["points"]) <= 32
        assert all(set(point) == {"lat", "lon"} for point in line["points"])
        assert all(
            coordinate == round(coordinate, 3)
            for point in line["points"]
            for coordinate in point.values()
        )
