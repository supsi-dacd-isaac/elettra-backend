"""Unit tests for yearly-analysis emissions lifecycle phase breakdown.

These tests validate that GET /api/v1/yearly-analysis/{id}/emissions returns
lifecycle phase fields (direct, directNonExhaust, energyChain, maintenance,
vehicle, endOfLife, infrastructure) when the external LCA API provides them.

Run with: pytest tests/test_yearly_emissions_phases.py -v
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from main import app
from app.routers.yearly_analysis import (
    _scale_indicator_phases,
    _allocate_phases_by_share,
    _bus_length_to_size_prefix,
    _resolve_bus_size_class,
    _get_configured_diesel_lca_vehicle,
    _LCA_PHASES,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MOCK_LCA_IMPACT_EBUS = {
    "gwp100a": {
        "direct": 0.0,
        "directNonExhaust": 0.025,
        "energyChain": 0.642,
        "maintenance": 0.112,
        "vehicle": 0.240,
        "endOfLife": 0.036,
        "infrastructure": 0.0092,
    },
    "nox": {
        "direct": 0.0,
        "directNonExhaust": 0.5,
        "energyChain": 1.2,
        "maintenance": 0.3,
        "vehicle": 0.4,
        "endOfLife": 0.1,
        "infrastructure": 0.05,
    },
    "pm10": {
        "direct": 0.0,
        "directNonExhaust": 0.8,
        "energyChain": 0.3,
        "maintenance": 0.15,
        "vehicle": 0.2,
        "endOfLife": 0.05,
        "infrastructure": 0.02,
    },
    "primaryEnergy": {
        "direct": 0.0,
        "directNonExhaust": 0.001,
        "energyChain": 0.08,
        "maintenance": 0.012,
        "vehicle": 0.03,
        "endOfLife": 0.004,
        "infrastructure": 0.001,
    },
    "primaryEnergyNonRenewable": {
        "direct": 0.0,
        "directNonExhaust": 0.0008,
        "energyChain": 0.05,
        "maintenance": 0.01,
        "vehicle": 0.025,
        "endOfLife": 0.003,
        "infrastructure": 0.0008,
    },
}

MOCK_LCA_IMPACT_DIESEL = {
    "gwp100a": {
        "direct": 0.95,
        "directNonExhaust": 0.025,
        "energyChain": 0.28,
        "maintenance": 0.08,
        "vehicle": 0.18,
        "endOfLife": 0.03,
        "infrastructure": 0.0092,
    },
    "nox": {
        "direct": 5.0,
        "directNonExhaust": 0.5,
        "energyChain": 0.8,
        "maintenance": 0.25,
        "vehicle": 0.35,
        "endOfLife": 0.08,
        "infrastructure": 0.04,
    },
    "pm10": {
        "direct": 0.5,
        "directNonExhaust": 0.8,
        "energyChain": 0.2,
        "maintenance": 0.12,
        "vehicle": 0.15,
        "endOfLife": 0.04,
        "infrastructure": 0.015,
    },
    "primaryEnergy": {
        "direct": 0.0,
        "directNonExhaust": 0.001,
        "energyChain": 0.12,
        "maintenance": 0.01,
        "vehicle": 0.025,
        "endOfLife": 0.003,
        "infrastructure": 0.001,
    },
    "primaryEnergyNonRenewable": {
        "direct": 0.0,
        "directNonExhaust": 0.0008,
        "energyChain": 0.1,
        "maintenance": 0.008,
        "vehicle": 0.02,
        "endOfLife": 0.002,
        "infrastructure": 0.0007,
    },
}


PHASE_KEYS = ["direct", "directNonExhaust", "energyChain", "maintenance",
              "vehicle", "endOfLife", "infrastructure"]

REAL_LCA_13M_BEV_RESPONSE = {
    "primaryEnergy": {
        "direct": 0.11880782257739521,
        "directNonExhaust": 0,
        "energyChain": 0.2719398974962534,
        "maintenance": 0.025558622345337026,
        "vehicle": 0.05106081164932562,
        "endOfLife": 0.0020701975894537096,
        "infrastructure": 0.05696717359193241,
    },
    "primaryEnergyNonRenewable": {
        "direct": 0.11880782257739521,
        "directNonExhaust": 0,
        "energyChain": 0.18360759714466984,
        "maintenance": 0.021310680731640504,
        "vehicle": 0.04650473278958328,
        "endOfLife": 0.0019124103553241128,
        "infrastructure": 0.05426720355097231,
    },
    "gwp100a": {
        "direct": 0,
        "directNonExhaust": 0,
        "energyChain": 27.581873872828606,
        "maintenance": 2.738858714285714,
        "vehicle": 15.850305264465577,
        "endOfLife": 1.0089379176425408,
        "infrastructure": 9.240785765992799,
    },
    "pm10": {
        "direct": 0,
        "directNonExhaust": 3.4933632923462636,
        "energyChain": 3.6390140152252446,
        "maintenance": 0.7422223490304709,
        "vehicle": 9.936132249880858,
        "endOfLife": 0.09224382386781058,
        "infrastructure": 5.4520144685188825,
    },
    "nox": {
        "direct": 0,
        "directNonExhaust": 0,
        "energyChain": 72.46076474416133,
        "maintenance": 2.893457142857143,
        "vehicle": 43.42982505952381,
        "endOfLife": 1.4167095816326533,
        "infrastructure": 25.69791544642857,
    },
}


# ---------------------------------------------------------------------------
# Pure function tests
# ---------------------------------------------------------------------------

class TestScaleIndicatorPhases:
    """Tests for _scale_indicator_phases helper (used by environmental endpoint)."""

    def test_scales_all_phases(self):
        breakdown = {
            "direct": 0.0,
            "directNonExhaust": 0.025,
            "energyChain": 0.642,
            "maintenance": 0.112,
            "vehicle": 0.240,
            "endOfLife": 0.036,
            "infrastructure": 0.0092,
        }
        factor = 1_000_000.0  # 1M pkm
        result = _scale_indicator_phases(breakdown, factor)

        assert result["direct"] == 0.0
        assert result["directNonExhaust"] == round(0.025 * factor, 4)
        assert result["energyChain"] == round(0.642 * factor, 4)
        assert result["maintenance"] == round(0.112 * factor, 4)
        assert result["vehicle"] == round(0.240 * factor, 4)
        assert result["endOfLife"] == round(0.036 * factor, 4)
        assert result["infrastructure"] == round(0.0092 * factor, 4)
        expected_total = sum(v * factor for v in breakdown.values())
        assert abs(result["total"] - round(expected_total, 4)) < 0.01

    def test_handles_missing_phases(self):
        breakdown = {"energyChain": 0.5, "vehicle": 0.3}
        result = _scale_indicator_phases(breakdown, 100.0)

        assert result["energyChain"] == round(0.5 * 100, 4)
        assert result["vehicle"] == round(0.3 * 100, 4)
        assert result["direct"] is None
        assert result["maintenance"] is None
        assert result["total"] == round(0.8 * 100, 4)

    def test_handles_empty_breakdown(self):
        result = _scale_indicator_phases({}, 1000.0)
        for phase in _LCA_PHASES:
            assert result[phase] is None
        assert result["total"] == 0.0

    def test_zero_factor(self):
        breakdown = {"energyChain": 0.5, "vehicle": 0.3}
        result = _scale_indicator_phases(breakdown, 0.0)
        assert result["energyChain"] == 0.0
        assert result["vehicle"] == 0.0
        assert result["total"] == 0.0


class TestAllocatePhasesByShare:
    """Tests for _allocate_phases_by_share (phase-share allocation for yearly emissions)."""

    def test_allocates_proportionally(self):
        breakdown = {
            "direct": 0.0,
            "directNonExhaust": 0.0,
            "energyChain": 27.58,
            "maintenance": 2.74,
            "vehicle": 15.85,
            "endOfLife": 1.01,
            "infrastructure": 9.24,
        }
        electric_total = 8_360_023.0
        result = _allocate_phases_by_share(breakdown, electric_total)

        raw_sum = sum(breakdown.values())
        assert result["direct"] == 0.0
        assert result["directNonExhaust"] == 0.0
        assert result["energyChain"] == round((27.58 / raw_sum) * electric_total, 4)
        assert result["maintenance"] == round((2.74 / raw_sum) * electric_total, 4)
        assert result["vehicle"] == round((15.85 / raw_sum) * electric_total, 4)

        phase_sum = sum(result[k] for k in PHASE_KEYS if result.get(k) is not None)
        assert abs(phase_sum - electric_total) < 1.0

    def test_total_equals_operational(self):
        """Phase sum equals the operational total passed in."""
        breakdown = {"energyChain": 10.0, "vehicle": 5.0, "maintenance": 2.0}
        op_total = 1_000_000.0
        result = _allocate_phases_by_share(breakdown, op_total)
        assert abs(result["total"] - op_total) < 1.0

    def test_preserves_zero_phases(self):
        """Real zero values (direct=0 for BEV) are preserved as 0.0, not None."""
        breakdown = {
            "direct": 0.0,
            "directNonExhaust": 0.0,
            "energyChain": 20.0,
            "maintenance": 3.0,
            "vehicle": 10.0,
            "endOfLife": 1.0,
            "infrastructure": 5.0,
        }
        result = _allocate_phases_by_share(breakdown, 500_000.0)
        assert result["direct"] == 0.0
        assert result["directNonExhaust"] == 0.0
        assert result["energyChain"] > 0

    def test_empty_breakdown_returns_nulls(self):
        """No LCA data → null phases, total equals operational total."""
        result = _allocate_phases_by_share({}, 1_000_000.0)
        for phase in _LCA_PHASES:
            assert result[phase] is None
        assert result["total"] == 1_000_000.0

    def test_all_zero_breakdown_returns_nulls(self):
        """All phases zero (raw_sum=0) → null phases, total equals operational."""
        breakdown = {p: 0.0 for p in _LCA_PHASES}
        result = _allocate_phases_by_share(breakdown, 500_000.0)
        for phase in _LCA_PHASES:
            assert result[phase] is None
        assert result["total"] == 500_000.0

    def test_missing_phases_are_none(self):
        """Phases not in the breakdown are None."""
        breakdown = {"energyChain": 10.0, "vehicle": 5.0}
        result = _allocate_phases_by_share(breakdown, 100_000.0)
        assert result["energyChain"] is not None
        assert result["vehicle"] is not None
        assert result["direct"] is None
        assert result["maintenance"] is None

    def test_real_13m_fixture_allocation(self):
        """Realistic 13m BEV fixture: phase shares sum to electric total."""
        breakdown = REAL_LCA_13M_BEV_RESPONSE["gwp100a"]
        electric_total = 8_360_023.1936
        result = _allocate_phases_by_share(breakdown, electric_total)

        raw_sum = sum(breakdown.values())
        expected_energy_chain = (breakdown["energyChain"] / raw_sum) * electric_total
        assert abs(result["energyChain"] - round(expected_energy_chain, 4)) < 0.01

        phase_sum = sum(result[k] for k in PHASE_KEYS if result.get(k) is not None)
        assert abs(phase_sum - electric_total) < 1.0
        assert abs(result["total"] - phase_sum) < 0.01


class TestBusLengthToSizePrefix:
    """Tests for _bus_length_to_size_prefix fallback mapping."""

    def test_9m(self):
        assert _bus_length_to_size_prefix(9) == "9m"
        assert _bus_length_to_size_prefix(8.5) == "9m"
        assert _bus_length_to_size_prefix(10) == "9m"

    def test_13m(self):
        assert _bus_length_to_size_prefix(12) == "13m"
        assert _bus_length_to_size_prefix(13) == "13m"
        assert _bus_length_to_size_prefix(14) == "13m"
        assert _bus_length_to_size_prefix(11) == "13m"

    def test_18m(self):
        assert _bus_length_to_size_prefix(18) == "18m"
        assert _bus_length_to_size_prefix(15) == "18m"
        assert _bus_length_to_size_prefix(24) == "18m"

    def test_none(self):
        assert _bus_length_to_size_prefix(None) is None

    def test_float_conversion(self):
        assert _bus_length_to_size_prefix(12.0) == "13m"
        assert _bus_length_to_size_prefix(18.5) == "18m"


class TestRealLcaApiResponse:
    """Test parser with realistic fixture captured from the real external LCA API.

    The values in REAL_LCA_13M_BEV_RESPONSE are real per-pkm values from
    GET /vehicle/{id}/impact for a 13m-city BEV depot 2020 vehicle.
    """

    def test_phase_share_allocation_gwp100a(self):
        """Phase-share allocation distributes electric total using Mobitool proportions."""
        electric_total = 8_360_023.1936
        result = _allocate_phases_by_share(
            REAL_LCA_13M_BEV_RESPONSE["gwp100a"], electric_total
        )

        raw = REAL_LCA_13M_BEV_RESPONSE["gwp100a"]
        raw_sum = sum(raw.values())

        assert result["direct"] == 0.0
        assert result["directNonExhaust"] == 0.0
        expected_ec = round((raw["energyChain"] / raw_sum) * electric_total, 4)
        assert result["energyChain"] == expected_ec
        assert result["energyChain"] > 4_000_000
        assert result["vehicle"] > 2_000_000
        assert result["maintenance"] > 400_000

        phase_sum = sum(result[k] for k in PHASE_KEYS if result.get(k) is not None)
        assert abs(phase_sum - electric_total) < 1.0

    def test_all_indicators_allocate_correctly(self):
        """All 5 indicators produce phase sums equal to their respective electric totals."""
        electric_totals = {
            "gwp100a": 8_360_023.0,
            "nox": 5_225_014.0,
            "pm10": 783_752.0,
            "primaryEnergy": 685_783.0,
            "primaryEnergyNonRenewable": 195_938.0,
        }
        for ind, el_total in electric_totals.items():
            result = _allocate_phases_by_share(
                REAL_LCA_13M_BEV_RESPONSE[ind], el_total
            )
            phase_sum = sum(result[k] for k in PHASE_KEYS if result.get(k) is not None)
            assert abs(phase_sum - el_total) < 1.0, (
                f"Indicator {ind}: phase_sum={phase_sum}, expected≈{el_total}"
            )


# ---------------------------------------------------------------------------
# Integration tests with mocked LCA API
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _get_auth_token(client: TestClient) -> str | None:
    import os
    credentials = [
        (os.getenv("TEST_LOGIN_EMAIL", "test@supsi.ch"),
         os.getenv("TEST_LOGIN_PASSWORD", ">tha0-!UdLb.hZ@aP)*x")),
        ("test01.elettra@fart.ch", "elettra"),
        ("test@tplsa.ch", "Elettra123!"),
    ]
    for email, password in credentials:
        response = client.post("/auth/login", json={"email": email, "password": password})
        if response.status_code == 200:
            return response.json().get("access_token")
    return None


def _get_user_id(client: TestClient, token: str) -> str | None:
    headers = {"Authorization": f"Bearer {token}"}
    resp = client.get("/auth/me", headers=headers)
    if resp.status_code == 200:
        return resp.json().get("id")
    return None


@pytest.fixture(scope="module")
def auth_data(client):
    """Get token and user_id, or skip the module."""
    token = _get_auth_token(client)
    if not token:
        pytest.skip("Could not authenticate")
    user_id = _get_user_id(client, token)
    if not user_id:
        pytest.skip("Could not get user_id")
    return {"token": token, "user_id": user_id}


@pytest.fixture(scope="module")
def test_yearly_analysis(client, auth_data):
    """Create a yearly analysis with prediction runs for testing."""
    import asyncpg
    import asyncio
    import json as json_mod

    from app.core.config import get_cached_settings
    settings = get_cached_settings()
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")

    async def setup():
        conn = await asyncpg.connect(dsn)
        try:
            user_id = auth_data["user_id"]

            bus_model_id = await conn.fetchval(
                """SELECT id FROM buses_models WHERE user_id = $1::uuid LIMIT 1""",
                user_id,
            )
            if bus_model_id is None:
                bus_model_id = await conn.fetchval(
                    """INSERT INTO buses_models (name, specs, user_id)
                       VALUES ('Test 12m eBus', '{"size": "13m Standard"}'::jsonb, $1::uuid)
                       RETURNING id""",
                    user_id,
                )

            shift_id = await conn.fetchval(
                """SELECT s.id FROM shifts s
                   JOIN buses b ON b.id = s.bus_id
                   WHERE b.user_id = $1::uuid LIMIT 1""",
                user_id,
            )
            if shift_id is None:
                shift_id = await conn.fetchval(
                    """SELECT id FROM shifts LIMIT 1"""
                )
            if shift_id is None:
                pytest.skip("No shifts available for testing")

            opt_run_id = await conn.fetchval(
                """INSERT INTO optimization_runs (user_id, bus_model_id, mode, status, input_params)
                   VALUES ($1::uuid, $2::uuid, 'joint', 'completed', '{}'::jsonb)
                   RETURNING id""",
                user_id, bus_model_id,
            )

            scenarios = [
                {"temperature": -10, "occurrences": 30},
                {"temperature": 0, "occurrences": 60},
                {"temperature": 10, "occurrences": 90},
                {"temperature": 20, "occurrences": 120},
                {"temperature": 30, "occurrences": 65},
            ]
            features = json_mod.dumps({
                "scenarios": scenarios,
                "config": {"auxiliary_heating_type": "default"},
            })

            ya_id = await conn.fetchval(
                """INSERT INTO yearly_analysis (optimization_run_id, name, features)
                   VALUES ($1::uuid, 'Phase Test YA', $2::jsonb)
                   RETURNING id""",
                opt_run_id, features,
            )

            for sc in scenarios:
                temp = sc["temperature"]
                summary = json_mod.dumps({
                    "total_consumption_kwh": 250.0 - temp * 2,
                    "total_distance_km": 180.0,
                    "total_auxiliary_kwh": max(0, 50.0 - temp * 3),
                    "total_drivetrain_kwh": 200.0,
                })
                await conn.execute(
                    """INSERT INTO prediction_runs
                       (user_id, shift_id, bus_model_id, yearly_analysis_id,
                        model_name, external_temp_celsius, auxiliary_heating_type,
                        occupancy_percent, summary, status)
                       VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid,
                               'test_model', $5, 'default', 50, $6::jsonb, 'completed')""",
                    user_id, shift_id, bus_model_id, ya_id,
                    temp, summary,
                )

            return {
                "ya_id": str(ya_id),
                "opt_run_id": str(opt_run_id),
                "bus_model_id": str(bus_model_id),
            }
        finally:
            await conn.close()

    result = asyncio.get_event_loop().run_until_complete(setup())
    yield result

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                "DELETE FROM prediction_runs WHERE yearly_analysis_id = $1::uuid",
                result["ya_id"],
            )
            await conn.execute(
                "DELETE FROM yearly_analysis WHERE id = $1::uuid",
                result["ya_id"],
            )
            await conn.execute(
                "DELETE FROM optimization_runs WHERE id = $1::uuid",
                result["opt_run_id"],
            )
        finally:
            await conn.close()

    asyncio.get_event_loop().run_until_complete(teardown())


@pytest.fixture(scope="module")
def test_diesel_yearly_analysis(client, auth_data):
    """Create a yearly analysis with diesel heating prediction runs."""
    import asyncpg
    import asyncio
    import json as json_mod

    from app.core.config import get_cached_settings
    settings = get_cached_settings()
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")

    async def setup():
        conn = await asyncpg.connect(dsn)
        try:
            user_id = auth_data["user_id"]

            bus_model_id = await conn.fetchval(
                """SELECT id FROM buses_models WHERE user_id = $1::uuid LIMIT 1""",
                user_id,
            )
            if bus_model_id is None:
                bus_model_id = await conn.fetchval(
                    """INSERT INTO buses_models (name, specs, user_id)
                       VALUES ('Test 12m eBus Diesel', '{"size": "13m Standard"}'::jsonb, $1::uuid)
                       RETURNING id""",
                    user_id,
                )

            shift_id = await conn.fetchval("""SELECT id FROM shifts LIMIT 1""")
            if shift_id is None:
                pytest.skip("No shifts available")

            opt_run_id = await conn.fetchval(
                """INSERT INTO optimization_runs (user_id, bus_model_id, mode, status, input_params)
                   VALUES ($1::uuid, $2::uuid, 'joint', 'completed', '{}'::jsonb)
                   RETURNING id""",
                user_id, bus_model_id,
            )

            scenarios = [
                {"temperature": -10, "occurrences": 30},
                {"temperature": 0, "occurrences": 60},
                {"temperature": 10, "occurrences": 90},
                {"temperature": 20, "occurrences": 120},
            ]
            features = json_mod.dumps({
                "scenarios": scenarios,
                "config": {"auxiliary_heating_type": "diesel"},
            })

            ya_id = await conn.fetchval(
                """INSERT INTO yearly_analysis (optimization_run_id, name, features)
                   VALUES ($1::uuid, 'Phase Test YA Diesel', $2::jsonb)
                   RETURNING id""",
                opt_run_id, features,
            )

            for sc in scenarios:
                temp = sc["temperature"]
                diesel_liters = max(0, 5.0 - temp * 0.3)
                summary = json_mod.dumps({
                    "total_consumption_kwh": 250.0 - temp * 2,
                    "total_distance_km": 180.0,
                    "total_auxiliary_kwh": max(0, 50.0 - temp * 3),
                    "total_drivetrain_kwh": 200.0,
                    "diesel_heating": {
                        "diesel_fuel_kwh": diesel_liters * 9.8,
                        "diesel_liters": diesel_liters,
                        "diesel_heater_efficiency": 0.85,
                    } if diesel_liters > 0 else None,
                })
                await conn.execute(
                    """INSERT INTO prediction_runs
                       (user_id, shift_id, bus_model_id, yearly_analysis_id,
                        model_name, external_temp_celsius, auxiliary_heating_type,
                        occupancy_percent, summary, status)
                       VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid,
                               'test_model', $5, 'diesel', 50, $6::jsonb, 'completed')""",
                    user_id, shift_id, bus_model_id, ya_id,
                    temp, summary,
                )

            return {
                "ya_id": str(ya_id),
                "opt_run_id": str(opt_run_id),
                "bus_model_id": str(bus_model_id),
            }
        finally:
            await conn.close()

    result = asyncio.get_event_loop().run_until_complete(setup())
    yield result

    async def teardown():
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                "DELETE FROM prediction_runs WHERE yearly_analysis_id = $1::uuid",
                result["ya_id"],
            )
            await conn.execute(
                "DELETE FROM yearly_analysis WHERE id = $1::uuid",
                result["ya_id"],
            )
            await conn.execute(
                "DELETE FROM optimization_runs WHERE id = $1::uuid",
                result["opt_run_id"],
            )
        finally:
            await conn.close()

    asyncio.get_event_loop().run_until_complete(teardown())


class TestEmissionsPhaseIntegration:
    """Integration tests verifying phase fields in the emissions response."""

    def test_emissions_returns_total(self, client, auth_data, test_yearly_analysis):
        """Basic: emissions endpoint returns ebus.gwp100a.total."""
        headers = {"Authorization": f"Bearer {auth_data['token']}"}
        resp = client.get(
            f"/api/v1/yearly-analysis/{test_yearly_analysis['ya_id']}/emissions",
            params={"bus_length_m": 12},
            headers=headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "ebus" in data
        assert "gwp100a" in data["ebus"]
        assert "total" in data["ebus"]["gwp100a"]
        assert data["ebus"]["gwp100a"]["total"] >= 0

    def test_emissions_includes_phase_keys_with_lca(self, client, auth_data, test_yearly_analysis):
        """When LCA API provides phase data, response includes phase keys with phase-share allocation."""
        headers = {"Authorization": f"Bearer {auth_data['token']}"}

        with patch(
            "app.routers.yearly_analysis._lca_get_impact",
            new_callable=AsyncMock,
        ) as mock_lca, patch(
            "app.routers.yearly_analysis._resolve_lca_vehicle",
            new_callable=AsyncMock,
        ) as mock_resolve, patch(
            "app.routers.yearly_analysis._get_configured_diesel_lca_vehicle",
            return_value=None,
        ):
            mock_ebus_lca = MagicMock()
            mock_ebus_lca.id = uuid4()
            mock_ebus_lca.passenger_capacity = 50
            mock_ebus_lca.functional_unit = "pkm"

            mock_resolve.return_value = mock_ebus_lca
            mock_lca.return_value = MOCK_LCA_IMPACT_EBUS

            resp = client.get(
                f"/api/v1/yearly-analysis/{test_yearly_analysis['ya_id']}/emissions",
                params={"bus_length_m": 12},
                headers=headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        gwp = data["ebus"]["gwp100a"]

        assert "total" in gwp
        assert gwp["total"] > 0
        for key in PHASE_KEYS:
            assert key in gwp, f"Missing phase key '{key}' in ebus.gwp100a"

        at_least_one_phase = any(
            gwp.get(k) is not None and gwp.get(k) > 0
            for k in PHASE_KEYS
        )
        assert at_least_one_phase, "At least one phase key should be > 0"

        # Phase-share: sum of phases ≈ electric
        phase_sum = sum(gwp[k] for k in PHASE_KEYS if gwp.get(k) is not None)
        assert abs(phase_sum - gwp["electric"]) < 1.0, (
            f"phase_sum={phase_sum} should ≈ electric={gwp['electric']}"
        )

    def test_all_indicators_have_phases_with_lca(self, client, auth_data, test_yearly_analysis):
        """All indicators should get phase data when LCA API responds."""
        headers = {"Authorization": f"Bearer {auth_data['token']}"}

        with patch(
            "app.routers.yearly_analysis._lca_get_impact",
            new_callable=AsyncMock,
        ) as mock_lca, patch(
            "app.routers.yearly_analysis._resolve_lca_vehicle",
            new_callable=AsyncMock,
        ) as mock_resolve, patch(
            "app.routers.yearly_analysis._get_configured_diesel_lca_vehicle",
            return_value=None,
        ):
            mock_ebus_lca = MagicMock()
            mock_ebus_lca.id = uuid4()
            mock_ebus_lca.passenger_capacity = 45
            mock_ebus_lca.functional_unit = "pkm"

            mock_resolve.return_value = mock_ebus_lca
            mock_lca.return_value = MOCK_LCA_IMPACT_EBUS

            resp = client.get(
                f"/api/v1/yearly-analysis/{test_yearly_analysis['ya_id']}/emissions",
                params={"bus_length_m": 12},
                headers=headers,
            )

        assert resp.status_code == 200
        data = resp.json()

        indicators = ["gwp100a", "nox", "pm10", "primaryEnergy", "primaryEnergyNonRenewable"]
        for ind in indicators:
            ebus_ind = data["ebus"][ind]
            assert ebus_ind["total"] > 0, f"ebus.{ind}.total should be > 0"
            has_phase = any(
                ebus_ind.get(k) is not None and ebus_ind.get(k) > 0
                for k in PHASE_KEYS
            )
            assert has_phase, f"ebus.{ind} should have at least one positive phase"

            # Phase-share: sum of phases ≈ electric for each indicator
            phase_sum = sum(
                ebus_ind[k] for k in PHASE_KEYS if ebus_ind.get(k) is not None
            )
            assert abs(phase_sum - ebus_ind["electric"]) < 1.0, (
                f"ebus.{ind}: phase_sum={phase_sum} ≠ electric={ebus_ind['electric']}"
            )

    def test_diesel_comparator_null_phases_without_lca(self, client, auth_data, test_yearly_analysis):
        """diesel_comparator has null phases when no diesel LCA vehicle config available."""
        headers = {"Authorization": f"Bearer {auth_data['token']}"}

        with patch(
            "app.routers.yearly_analysis._lca_get_impact",
            new_callable=AsyncMock,
        ) as mock_lca, patch(
            "app.routers.yearly_analysis._resolve_lca_vehicle",
            new_callable=AsyncMock,
        ) as mock_resolve, patch(
            "app.routers.yearly_analysis._get_configured_diesel_lca_vehicle",
            return_value=None,
        ):
            mock_ebus_lca = MagicMock()
            mock_ebus_lca.id = uuid4()
            mock_ebus_lca.passenger_capacity = 50
            mock_ebus_lca.functional_unit = "pkm"

            mock_resolve.return_value = mock_ebus_lca
            mock_lca.return_value = MOCK_LCA_IMPACT_EBUS

            resp = client.get(
                f"/api/v1/yearly-analysis/{test_yearly_analysis['ya_id']}/emissions",
                params={"bus_length_m": 12},
                headers=headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        dc_gwp = data["diesel_comparator"]["gwp100a"]

        assert dc_gwp["total"] > 0
        for key in PHASE_KEYS:
            assert dc_gwp.get(key) is None, (
                f"diesel_comparator.gwp100a.{key} should be null without diesel LCA vehicle"
            )

    def test_graceful_degradation_no_lca(self, client, auth_data, test_yearly_analysis):
        """When LCA vehicle cannot be resolved, response still works (total-only)."""
        headers = {"Authorization": f"Bearer {auth_data['token']}"}

        with patch(
            "app.routers.yearly_analysis._resolve_lca_vehicle",
            new_callable=AsyncMock,
            return_value=None,
        ), patch(
            "app.routers.yearly_analysis._get_configured_diesel_lca_vehicle",
            return_value=None,
        ):
            resp = client.get(
                f"/api/v1/yearly-analysis/{test_yearly_analysis['ya_id']}/emissions",
                params={"bus_length_m": 12},
                headers=headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        gwp = data["ebus"]["gwp100a"]

        assert gwp["total"] > 0
        assert gwp["electric"] > 0
        for key in PHASE_KEYS:
            assert gwp.get(key) is None

    def test_diesel_heating_does_not_crash(self, client, auth_data, test_diesel_yearly_analysis):
        """Diesel-heating yearly analysis does not crash when LCA data is present."""
        headers = {"Authorization": f"Bearer {auth_data['token']}"}

        with patch(
            "app.routers.yearly_analysis._lca_get_impact",
            new_callable=AsyncMock,
        ) as mock_lca, patch(
            "app.routers.yearly_analysis._resolve_lca_vehicle",
            new_callable=AsyncMock,
        ) as mock_resolve, patch(
            "app.routers.yearly_analysis._get_configured_diesel_lca_vehicle",
            return_value=None,
        ):
            mock_ebus_lca = MagicMock()
            mock_ebus_lca.id = uuid4()
            mock_ebus_lca.passenger_capacity = 50
            mock_ebus_lca.functional_unit = "pkm"

            mock_resolve.return_value = mock_ebus_lca
            mock_lca.return_value = MOCK_LCA_IMPACT_EBUS

            resp = client.get(
                f"/api/v1/yearly-analysis/{test_diesel_yearly_analysis['ya_id']}/emissions",
                params={"bus_length_m": 12},
                headers=headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["auxiliary_heating_type"] == "diesel"
        assert data["assumptions"]["auxiliary_heating_type"] == "diesel"

        gwp = data["ebus"]["gwp100a"]
        assert gwp["total"] > 0
        assert gwp["diesel_heating"] > 0
        assert gwp.get("energyChain") is not None
        assert gwp["energyChain"] > 0

    def test_diesel_heating_not_injected_into_phases(self, client, auth_data, test_diesel_yearly_analysis):
        """diesel_heating is NOT attributed to any lifecycle phase.

        Phase-share allocation distributes only the electric-side total across
        phases.  diesel_heating is added to the overall total but not to any phase.
        """
        headers = {"Authorization": f"Bearer {auth_data['token']}"}

        with patch(
            "app.routers.yearly_analysis._lca_get_impact",
            new_callable=AsyncMock,
        ) as mock_lca, patch(
            "app.routers.yearly_analysis._resolve_lca_vehicle",
            new_callable=AsyncMock,
        ) as mock_resolve, patch(
            "app.routers.yearly_analysis._get_configured_diesel_lca_vehicle",
            return_value=None,
        ):
            mock_ebus_lca = MagicMock()
            mock_ebus_lca.id = uuid4()
            mock_ebus_lca.passenger_capacity = 50
            mock_ebus_lca.functional_unit = "pkm"

            mock_resolve.return_value = mock_ebus_lca
            mock_lca.return_value = MOCK_LCA_IMPACT_EBUS

            resp = client.get(
                f"/api/v1/yearly-analysis/{test_diesel_yearly_analysis['ya_id']}/emissions",
                params={"bus_length_m": 12},
                headers=headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        gwp = data["ebus"]["gwp100a"]

        dh = gwp["diesel_heating"]
        assert dh > 0

        phase_sum = sum(
            gwp[k] for k in PHASE_KEYS if gwp.get(k) is not None
        )
        # phase_sum ≈ electric (not total)
        assert abs(phase_sum - gwp["electric"]) < 1.0, (
            f"phase_sum={phase_sum}, electric={gwp['electric']}"
        )
        # total = phase_sum + diesel_heating
        assert abs(gwp["total"] - (phase_sum + dh)) < 1.0, (
            f"total={gwp['total']}, phase_sum={phase_sum}, dh={dh}"
        )
        # phases alone do NOT include diesel_heating
        assert phase_sum < gwp["total"]

    def test_total_equals_phase_sum_default_mode(self, client, auth_data, test_yearly_analysis):
        """In default (no diesel heating) mode, total == sum of phases == electric."""
        headers = {"Authorization": f"Bearer {auth_data['token']}"}

        with patch(
            "app.routers.yearly_analysis._lca_get_impact",
            new_callable=AsyncMock,
        ) as mock_lca, patch(
            "app.routers.yearly_analysis._resolve_lca_vehicle",
            new_callable=AsyncMock,
        ) as mock_resolve, patch(
            "app.routers.yearly_analysis._get_configured_diesel_lca_vehicle",
            return_value=None,
        ):
            mock_ebus_lca = MagicMock()
            mock_ebus_lca.id = uuid4()
            mock_ebus_lca.passenger_capacity = 50
            mock_ebus_lca.functional_unit = "pkm"

            mock_resolve.return_value = mock_ebus_lca
            mock_lca.return_value = MOCK_LCA_IMPACT_EBUS

            resp = client.get(
                f"/api/v1/yearly-analysis/{test_yearly_analysis['ya_id']}/emissions",
                params={"bus_length_m": 12},
                headers=headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        gwp = data["ebus"]["gwp100a"]

        phase_sum = sum(
            gwp[k] for k in PHASE_KEYS if gwp.get(k) is not None
        )
        # In default mode: total == phase_sum == electric (no diesel heating)
        assert abs(gwp["total"] - phase_sum) < 1.0, (
            f"total={gwp['total']}, phase_sum={phase_sum}"
        )
        assert abs(gwp["total"] - gwp["electric"]) < 1.0
        assert gwp["diesel_heating"] == 0.0

    def test_lca_phase_method_in_assumptions(self, client, auth_data, test_yearly_analysis):
        """assumptions.lca_phase_method documents the phase-share allocation method."""
        headers = {"Authorization": f"Bearer {auth_data['token']}"}

        with patch(
            "app.routers.yearly_analysis._lca_get_impact",
            new_callable=AsyncMock,
        ) as mock_lca, patch(
            "app.routers.yearly_analysis._resolve_lca_vehicle",
            new_callable=AsyncMock,
        ) as mock_resolve, patch(
            "app.routers.yearly_analysis._get_configured_diesel_lca_vehicle",
            return_value=None,
        ):
            mock_ebus_lca = MagicMock()
            mock_ebus_lca.id = uuid4()
            mock_ebus_lca.passenger_capacity = 42
            mock_ebus_lca.functional_unit = "pkm"

            mock_resolve.return_value = mock_ebus_lca
            mock_lca.return_value = MOCK_LCA_IMPACT_EBUS

            resp = client.get(
                f"/api/v1/yearly-analysis/{test_yearly_analysis['ya_id']}/emissions",
                params={"bus_length_m": 12},
                headers=headers,
            )

        assert resp.status_code == 200
        assumptions = resp.json()["assumptions"]
        assert assumptions["lca_phase_method"] == "mobitool_phase_share"
        assert assumptions["lca_source_functional_unit"] == "pkm"

    def test_diesel_heating_assumptions(self, client, auth_data, test_diesel_yearly_analysis):
        """Diesel-heating response includes auxiliary_heating_type: diesel in assumptions."""
        headers = {"Authorization": f"Bearer {auth_data['token']}"}

        resp = client.get(
            f"/api/v1/yearly-analysis/{test_diesel_yearly_analysis['ya_id']}/emissions",
            params={"bus_length_m": 12},
            headers=headers,
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["assumptions"]["auxiliary_heating_type"] == "diesel"
        assert data["assumptions"]["yearly_diesel_heating_liters"] > 0

    def test_backward_compatible_total_only_when_no_lca(self, client, auth_data, test_yearly_analysis):
        """Response remains backward-compatible for total-only payloads (no LCA data)."""
        headers = {"Authorization": f"Bearer {auth_data['token']}"}

        with patch(
            "app.routers.yearly_analysis._resolve_lca_vehicle",
            new_callable=AsyncMock,
            return_value=None,
        ):
            resp = client.get(
                f"/api/v1/yearly-analysis/{test_yearly_analysis['ya_id']}/emissions",
                params={"bus_length_m": 12},
                headers=headers,
            )

        assert resp.status_code == 200
        data = resp.json()

        assert "ebus" in data
        assert "diesel_comparator" in data
        assert "annual_saving" in data
        assert "assumptions" in data
        assert "scenarios" in data

        gwp = data["ebus"]["gwp100a"]
        assert "total" in gwp
        assert "electric" in gwp
        assert "diesel_heating" in gwp
        # Fallback: total = electric + diesel_heating
        assert abs(gwp["total"] - (gwp["electric"] + gwp["diesel_heating"])) < 0.01
        # Phases are null when no LCA data
        for key in PHASE_KEYS:
            assert gwp.get(key) is None

        # Assumptions reflect no LCA
        assert data["assumptions"]["lca_phase_method"] is None
        assert data["assumptions"]["lca_source_functional_unit"] is None


class TestCompletePayload:
    """Tests for the complete emissions payload structure."""

    def _get_response_with_lca(self, client, auth_data, ya_id):
        """Helper: call emissions with mocked LCA (ebus only), return JSON response."""
        headers = {"Authorization": f"Bearer {auth_data['token']}"}
        with patch(
            "app.routers.yearly_analysis._lca_get_impact",
            new_callable=AsyncMock,
        ) as mock_lca, patch(
            "app.routers.yearly_analysis._resolve_lca_vehicle",
            new_callable=AsyncMock,
        ) as mock_resolve, patch(
            "app.routers.yearly_analysis._get_configured_diesel_lca_vehicle",
            return_value=None,
        ):
            mock_ebus_lca = MagicMock()
            mock_ebus_lca.id = uuid4()
            mock_ebus_lca.passenger_capacity = 64
            mock_ebus_lca.functional_unit = "pkm"

            mock_resolve.return_value = mock_ebus_lca
            mock_lca.return_value = MOCK_LCA_IMPACT_EBUS

            resp = client.get(
                f"/api/v1/yearly-analysis/{ya_id}/emissions",
                params={"bus_length_m": 12},
                headers=headers,
            )
        assert resp.status_code == 200
        return resp.json()

    def _get_response_with_full_lca(self, client, auth_data, ya_id):
        """Helper: call emissions with mocked LCA (ebus + diesel), return JSON response."""
        headers = {"Authorization": f"Bearer {auth_data['token']}"}
        ebus_id = str(uuid4())
        diesel_cfg_id = "5efba16e-a9ca-4773-9c10-fab2e63a4906"

        async def lca_side_effect(vehicle_id):
            if vehicle_id == ebus_id:
                return MOCK_LCA_IMPACT_EBUS
            if vehicle_id == diesel_cfg_id:
                return MOCK_LCA_IMPACT_DIESEL
            return None

        with patch(
            "app.routers.yearly_analysis._lca_get_impact",
            new_callable=AsyncMock,
            side_effect=lca_side_effect,
        ), patch(
            "app.routers.yearly_analysis._resolve_lca_vehicle",
            new_callable=AsyncMock,
        ) as mock_resolve:
            mock_ebus_lca = MagicMock()
            mock_ebus_lca.id = ebus_id
            mock_ebus_lca.passenger_capacity = 64
            mock_ebus_lca.functional_unit = "pkm"

            mock_resolve.return_value = mock_ebus_lca

            resp = client.get(
                f"/api/v1/yearly-analysis/{ya_id}/emissions",
                params={"bus_length_m": 12},
                headers=headers,
            )
        assert resp.status_code == 200
        return resp.json()

    def test_complete_payload_shape(self, client, auth_data, test_yearly_analysis):
        """Endpoint returns all required top-level sections."""
        data = self._get_response_with_lca(
            client, auth_data, test_yearly_analysis["ya_id"]
        )
        required = [
            "assumptions", "indicators", "mixed_case_decomposition",
            "lifecycle_breakdown", "primary_energy_breakdown", "savings",
        ]
        for section in required:
            assert section in data and data[section] is not None, (
                f"Missing section: {section}"
            )

    def test_indicators_structure(self, client, auth_data, test_yearly_analysis):
        """indicators list has 5 entries with all required fields."""
        data = self._get_response_with_lca(
            client, auth_data, test_yearly_analysis["ya_id"]
        )
        indicators = data["indicators"]
        assert len(indicators) == 5

        expected_keys = {"gwp100a", "nox", "pm10", "primaryEnergy", "primaryEnergyNonRenewable"}
        actual_keys = {i["key"] for i in indicators}
        assert actual_keys == expected_keys

        for ind in indicators:
            assert "label" in ind
            assert "unit" in ind
            assert "display_unit" in ind
            assert ind["ebus_total"] > 0
            assert ind["diesel_comparator"] > 0
            assert "delta_vs_diesel" in ind
            assert "change_vs_diesel_percent" in ind
            assert "normalized_ebus_per_km" in ind
            assert "normalized_diesel_per_km" in ind
            assert "normalized_unit" in ind

    def test_indicator_delta_identity(self, client, auth_data, test_yearly_analysis):
        """delta_vs_diesel = diesel_comparator - ebus_total for each indicator."""
        data = self._get_response_with_lca(
            client, auth_data, test_yearly_analysis["ya_id"]
        )
        for ind in data["indicators"]:
            expected_delta = ind["diesel_comparator"] - ind["ebus_total"]
            assert abs(ind["delta_vs_diesel"] - expected_delta) < 1.0, (
                f"{ind['key']}: delta mismatch"
            )

    def test_normalized_values(self, client, auth_data, test_yearly_analysis):
        """Normalized per-km values are consistent."""
        data = self._get_response_with_lca(
            client, auth_data, test_yearly_analysis["ya_id"]
        )
        km = data["assumptions"]["yearly_distance_km"]
        for ind in data["indicators"]:
            expected_norm_ebus = ind["ebus_total"] / km
            assert abs(ind["normalized_ebus_per_km"] - round(expected_norm_ebus, 4)) < 0.01, (
                f"{ind['key']}: normalized ebus mismatch"
            )

    def test_mixed_case_decomposition_diesel(self, client, auth_data, test_diesel_yearly_analysis):
        """Mixed-case decomposition available for diesel heating."""
        data = self._get_response_with_lca(
            client, auth_data, test_diesel_yearly_analysis["ya_id"]
        )
        mc = data["mixed_case_decomposition"]
        assert mc["available"] is True
        assert mc["yearly_electric_kwh"] > 0
        assert mc["electric_kwh_per_100km"] > 0
        assert mc["yearly_diesel_heating_liters"] > 0

        for ind_key in ["gwp100a", "nox", "pm10", "primaryEnergy", "primaryEnergyNonRenewable"]:
            ind = mc["indicators"][ind_key]
            assert ind["electric_side"] > 0
            assert ind["diesel_heating"] > 0
            total_check = ind["electric_side"] + ind["diesel_heating"]
            assert abs(ind["total"] - total_check) < 0.01, (
                f"mixed_case {ind_key}: total ≠ electric + diesel_heating"
            )

    def test_lifecycle_breakdown_consistency(self, client, auth_data, test_diesel_yearly_analysis):
        """Lifecycle: phase_sum = electric_side, total = phase_sum + diesel_heating."""
        data = self._get_response_with_lca(
            client, auth_data, test_diesel_yearly_analysis["ya_id"]
        )
        lb = data["lifecycle_breakdown"]
        assert lb["indicator"] == "gwp100a"
        assert lb["method"] == "mobitool_phase_share"

        eb = lb["ebus"]
        assert eb["phase_sum"] is not None
        assert abs(eb["phase_sum"] - eb["electric_side"]) < 1.0
        assert abs(eb["total"] - (eb["phase_sum"] + eb["diesel_heating"])) < 1.0
        assert eb["phase_sum_represents"] == "electric_side_only"
        assert eb["diesel_heating"] > 0

    def test_lifecycle_diesel_comparator_unavailable(self, client, auth_data, test_yearly_analysis):
        """Diesel comparator lifecycle clearly states unavailability when config disabled."""
        data = self._get_response_with_lca(
            client, auth_data, test_yearly_analysis["ya_id"]
        )
        dc = data["lifecycle_breakdown"]["diesel_comparator"]
        assert dc["available"] is False
        assert dc["total"] > 0
        assert dc["phase_sum"] is None
        assert dc["reason"] == "configured_diesel_lca_vehicle_not_found"

    def test_primary_energy_split(self, client, auth_data, test_yearly_analysis):
        """renewable + non_renewable = total, percentages sum to 100."""
        data = self._get_response_with_lca(
            client, auth_data, test_yearly_analysis["ya_id"]
        )
        pe = data["primary_energy_breakdown"]
        for side_key in ["ebus", "diesel_comparator"]:
            side = pe[side_key]
            assert abs(side["renewable"] + side["non_renewable"] - side["total"]) < 0.1
            assert abs(side["renewable_percent"] + side["non_renewable_percent"] - 100.0) < 0.1

    def test_savings_items(self, client, auth_data, test_yearly_analysis):
        """Savings block has CO₂, NOx, PM₁₀, Primary energy with correct structure."""
        data = self._get_response_with_lca(
            client, auth_data, test_yearly_analysis["ya_id"]
        )
        items = data["savings"]["items"]
        assert len(items) == 4
        keys = [i["key"] for i in items]
        assert keys == ["gwp100a", "nox", "pm10", "primaryEnergy"]

        for item in items:
            assert item["diesel_display"] > item["ebus_display"]
            assert item["saved_display"] > 0
            assert item["saved_percent"] > 0

    def test_no_lca_lifecycle_shows_status(self, client, auth_data, test_yearly_analysis):
        """When no LCA, assumptions show unavailable status with reason."""
        headers = {"Authorization": f"Bearer {auth_data['token']}"}
        with patch(
            "app.routers.yearly_analysis._resolve_lca_vehicle",
            new_callable=AsyncMock,
            return_value=None,
        ), patch(
            "app.routers.yearly_analysis._get_configured_diesel_lca_vehicle",
            return_value=None,
        ):
            resp = client.get(
                f"/api/v1/yearly-analysis/{test_yearly_analysis['ya_id']}/emissions",
                params={"bus_length_m": 12},
                headers=headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["assumptions"]["lca_phase_status"] == "unavailable"
        assert data["assumptions"]["lca_phase_reason"] == "no_lca_vehicle_match"
        assert data["lifecycle_breakdown"]["ebus"]["phase_sum"] is None


# ---------------------------------------------------------------------------
# New tests: Size class mapping, unit conversions, diesel comparator lifecycle
# ---------------------------------------------------------------------------

class TestResolveBusSizeClass:
    """Tests for _resolve_bus_size_class helper."""

    def test_9m_class(self):
        assert _resolve_bus_size_class(8) == "9m"
        assert _resolve_bus_size_class(9) == "9m"
        assert _resolve_bus_size_class(10) == "9m"

    def test_12m_class(self):
        assert _resolve_bus_size_class(11) == "12m"
        assert _resolve_bus_size_class(12) == "12m"
        assert _resolve_bus_size_class(14) == "12m"

    def test_18m_class(self):
        assert _resolve_bus_size_class(15) == "18m"
        assert _resolve_bus_size_class(18) == "18m"
        assert _resolve_bus_size_class(24) == "18m"

    def test_none_input(self):
        assert _resolve_bus_size_class(None) is None


class TestConfiguredDieselLcaVehicle:
    """Tests for _get_configured_diesel_lca_vehicle helper."""

    def test_9m_returns_config(self):
        cfg = _get_configured_diesel_lca_vehicle("9m")
        assert cfg is not None
        assert cfg["id"] == "40ac537a-b112-40a0-8f73-c314237bc7e2"
        assert cfg["source_id"] == 207

    def test_12m_returns_config(self):
        cfg = _get_configured_diesel_lca_vehicle("12m")
        assert cfg is not None
        assert cfg["id"] == "5efba16e-a9ca-4773-9c10-fab2e63a4906"
        assert cfg["source_id"] == 217
        assert cfg["lca_size"] == "13m-city"

    def test_18m_returns_config(self):
        cfg = _get_configured_diesel_lca_vehicle("18m")
        assert cfg is not None
        assert cfg["id"] == "2b46af76-34e0-4b67-8631-0478d2d8b712"
        assert cfg["source_id"] == 238

    def test_unknown_size_returns_none(self):
        cfg = _get_configured_diesel_lca_vehicle("25m")
        assert cfg is None


class TestSavingsUnitConversions:
    """Tests for correct unit conversions in savings block."""

    def _get_savings_response(self, client, auth_data, ya_id):
        """Helper: get response with full LCA data for savings verification."""
        headers = {"Authorization": f"Bearer {auth_data['token']}"}
        ebus_id = str(uuid4())
        diesel_cfg_id = "5efba16e-a9ca-4773-9c10-fab2e63a4906"

        async def lca_side_effect(vehicle_id):
            if vehicle_id == ebus_id:
                return MOCK_LCA_IMPACT_EBUS
            if vehicle_id == diesel_cfg_id:
                return MOCK_LCA_IMPACT_DIESEL
            return None

        with patch(
            "app.routers.yearly_analysis._lca_get_impact",
            new_callable=AsyncMock,
            side_effect=lca_side_effect,
        ), patch(
            "app.routers.yearly_analysis._resolve_lca_vehicle",
            new_callable=AsyncMock,
        ) as mock_resolve:
            mock_ebus_lca = MagicMock()
            mock_ebus_lca.id = ebus_id
            mock_ebus_lca.passenger_capacity = 64
            mock_ebus_lca.functional_unit = "pkm"
            mock_resolve.return_value = mock_ebus_lca

            resp = client.get(
                f"/api/v1/yearly-analysis/{ya_id}/emissions",
                params={"bus_length_m": 12},
                headers=headers,
            )
        assert resp.status_code == 200
        return resp.json()

    def test_nox_savings_in_kg(self, client, auth_data, test_yearly_analysis):
        """NOx savings values are in kg/year (internal mg ÷ 1e6)."""
        data = self._get_savings_response(client, auth_data, test_yearly_analysis["ya_id"])
        nox_item = next(i for i in data["savings"]["items"] if i["key"] == "nox")
        assert nox_item["unit"] == "kg/year"
        # Internal NOx is mg/year; with realistic yearly kWh (~65k),
        # NOx ebus ≈ 65000 * 80 = 5.2M mg = 5.2 kg (order of magnitude)
        assert nox_item["ebus_display"] < 100, (
            f"NOx ebus should be kg, not mg: got {nox_item['ebus_display']}"
        )

    def test_pm10_savings_in_kg(self, client, auth_data, test_yearly_analysis):
        """PM₁₀ savings values are in kg/year (internal mg ÷ 1e6)."""
        data = self._get_savings_response(client, auth_data, test_yearly_analysis["ya_id"])
        pm10_item = next(i for i in data["savings"]["items"] if i["key"] == "pm10")
        assert pm10_item["unit"] == "kg/year"
        assert pm10_item["ebus_display"] < 10, (
            f"PM₁₀ ebus should be kg, not mg: got {pm10_item['ebus_display']}"
        )

    def test_co2_savings_in_tonnes(self, client, auth_data, test_yearly_analysis):
        """CO₂ savings values are in t/year (internal g ÷ 1e6)."""
        data = self._get_savings_response(client, auth_data, test_yearly_analysis["ya_id"])
        gwp_item = next(i for i in data["savings"]["items"] if i["key"] == "gwp100a")
        assert gwp_item["unit"] == "t/year"
        assert gwp_item["ebus_display"] < 1000, (
            f"CO₂ ebus should be t, not g: got {gwp_item['ebus_display']}"
        )

    def test_primary_energy_savings_in_gj(self, client, auth_data, test_yearly_analysis):
        """Primary energy savings values are in GJ/year (internal MJ ÷ 1e3)."""
        data = self._get_savings_response(client, auth_data, test_yearly_analysis["ya_id"])
        pe_item = next(i for i in data["savings"]["items"] if i["key"] == "primaryEnergy")
        assert pe_item["unit"] == "GJ/year"
        assert pe_item["ebus_display"] > 0

    def test_normalized_nox_in_mg_per_km(self, client, auth_data, test_yearly_analysis):
        """Normalized NOx is mg/km."""
        data = self._get_savings_response(client, auth_data, test_yearly_analysis["ya_id"])
        nox_ind = next(i for i in data["indicators"] if i["key"] == "nox")
        assert nox_ind["normalized_unit"] == "mg/km"
        # Internal NOx is mg/year ÷ km = mg/km; no conversion needed.
        assert nox_ind["normalized_ebus_per_km"] > 0

    def test_normalized_pm10_in_mg_per_km(self, client, auth_data, test_yearly_analysis):
        """Normalized PM₁₀ is mg/km."""
        data = self._get_savings_response(client, auth_data, test_yearly_analysis["ya_id"])
        pm10_ind = next(i for i in data["indicators"] if i["key"] == "pm10")
        assert pm10_ind["normalized_unit"] == "mg/km"
        assert pm10_ind["normalized_ebus_per_km"] > 0


class TestDieselComparatorLifecycle:
    """Tests for diesel comparator lifecycle phase-share allocation."""

    def _get_full_lca_response(self, client, auth_data, ya_id):
        """Helper: get response with both ebus and diesel LCA data."""
        headers = {"Authorization": f"Bearer {auth_data['token']}"}
        ebus_id = str(uuid4())
        diesel_cfg_id = "5efba16e-a9ca-4773-9c10-fab2e63a4906"

        async def lca_side_effect(vehicle_id):
            if vehicle_id == ebus_id:
                return MOCK_LCA_IMPACT_EBUS
            if vehicle_id == diesel_cfg_id:
                return MOCK_LCA_IMPACT_DIESEL
            return None

        with patch(
            "app.routers.yearly_analysis._lca_get_impact",
            new_callable=AsyncMock,
            side_effect=lca_side_effect,
        ), patch(
            "app.routers.yearly_analysis._resolve_lca_vehicle",
            new_callable=AsyncMock,
        ) as mock_resolve:
            mock_ebus_lca = MagicMock()
            mock_ebus_lca.id = ebus_id
            mock_ebus_lca.passenger_capacity = 64
            mock_ebus_lca.functional_unit = "pkm"
            mock_resolve.return_value = mock_ebus_lca

            resp = client.get(
                f"/api/v1/yearly-analysis/{ya_id}/emissions",
                params={"bus_length_m": 12},
                headers=headers,
            )
        assert resp.status_code == 200
        return resp.json()

    def test_diesel_comparator_available(self, client, auth_data, test_yearly_analysis):
        """With mocked diesel LCA API, diesel_comparator.available = true."""
        data = self._get_full_lca_response(client, auth_data, test_yearly_analysis["ya_id"])
        dc = data["lifecycle_breakdown"]["diesel_comparator"]
        assert dc["available"] is True
        assert dc["reason"] is None

    def test_diesel_comparator_phases_non_null(self, client, auth_data, test_yearly_analysis):
        """Diesel comparator phases are populated."""
        data = self._get_full_lca_response(client, auth_data, test_yearly_analysis["ya_id"])
        dc = data["lifecycle_breakdown"]["diesel_comparator"]
        phases = dc["phases"]
        at_least_one = any(
            phases.get(k) is not None and phases.get(k) > 0
            for k in PHASE_KEYS
        )
        assert at_least_one, "At least one diesel phase should be > 0"

    def test_diesel_comparator_phase_sum_equals_total(self, client, auth_data, test_yearly_analysis):
        """diesel_comparator.phase_sum ≈ diesel_comparator.total."""
        data = self._get_full_lca_response(client, auth_data, test_yearly_analysis["ya_id"])
        dc = data["lifecycle_breakdown"]["diesel_comparator"]
        assert dc["phase_sum"] is not None
        assert abs(dc["phase_sum"] - dc["total"]) < 1.0, (
            f"phase_sum={dc['phase_sum']} ≠ total={dc['total']}"
        )

    def test_diesel_comparator_metadata(self, client, auth_data, test_yearly_analysis):
        """Diesel comparator includes vehicle metadata from config."""
        data = self._get_full_lca_response(client, auth_data, test_yearly_analysis["ya_id"])
        dc = data["lifecycle_breakdown"]["diesel_comparator"]
        assert dc["size"] == "12m"
        assert dc["lca_size"] == "13m-city"
        assert dc["source_id"] == 217
        assert dc["name"] == "City busSingle deck13m-cityICEV-d2020"
        assert dc["lca_vehicle_id"] == "5efba16e-a9ca-4773-9c10-fab2e63a4906"

    def test_diesel_comparator_config_not_found(self, client, auth_data, test_yearly_analysis):
        """When config has no entry → available=false, reason=configured_diesel_lca_vehicle_not_found."""
        headers = {"Authorization": f"Bearer {auth_data['token']}"}
        with patch(
            "app.routers.yearly_analysis._lca_get_impact",
            new_callable=AsyncMock,
            return_value=MOCK_LCA_IMPACT_EBUS,
        ), patch(
            "app.routers.yearly_analysis._resolve_lca_vehicle",
            new_callable=AsyncMock,
        ) as mock_resolve, patch(
            "app.routers.yearly_analysis._get_configured_diesel_lca_vehicle",
            return_value=None,
        ):
            mock_ebus_lca = MagicMock()
            mock_ebus_lca.id = uuid4()
            mock_ebus_lca.passenger_capacity = 64
            mock_ebus_lca.functional_unit = "pkm"
            mock_resolve.return_value = mock_ebus_lca

            resp = client.get(
                f"/api/v1/yearly-analysis/{test_yearly_analysis['ya_id']}/emissions",
                params={"bus_length_m": 12},
                headers=headers,
            )
        assert resp.status_code == 200
        dc = resp.json()["lifecycle_breakdown"]["diesel_comparator"]
        assert dc["available"] is False
        assert dc["reason"] == "configured_diesel_lca_vehicle_not_found"

    def test_diesel_comparator_external_lca_error(self, client, auth_data, test_yearly_analysis):
        """When external LCA API fails → available=false, reason=external_lca_error."""
        headers = {"Authorization": f"Bearer {auth_data['token']}"}
        ebus_id = str(uuid4())

        async def lca_side_effect(vehicle_id):
            if vehicle_id == ebus_id:
                return MOCK_LCA_IMPACT_EBUS
            return None  # Diesel API call fails

        with patch(
            "app.routers.yearly_analysis._lca_get_impact",
            new_callable=AsyncMock,
            side_effect=lca_side_effect,
        ), patch(
            "app.routers.yearly_analysis._resolve_lca_vehicle",
            new_callable=AsyncMock,
        ) as mock_resolve:
            mock_ebus_lca = MagicMock()
            mock_ebus_lca.id = ebus_id
            mock_ebus_lca.passenger_capacity = 64
            mock_ebus_lca.functional_unit = "pkm"
            mock_resolve.return_value = mock_ebus_lca

            resp = client.get(
                f"/api/v1/yearly-analysis/{test_yearly_analysis['ya_id']}/emissions",
                params={"bus_length_m": 12},
                headers=headers,
            )
        assert resp.status_code == 200
        dc = resp.json()["lifecycle_breakdown"]["diesel_comparator"]
        assert dc["available"] is False
        assert dc["reason"] == "external_lca_error"

    def test_diesel_comparator_empty_phase_response(self, client, auth_data, test_yearly_analysis):
        """When LCA API returns non-dict for gwp100a → available=false, reason=empty_phase_response."""
        headers = {"Authorization": f"Bearer {auth_data['token']}"}
        ebus_id = str(uuid4())
        diesel_cfg_id = "5efba16e-a9ca-4773-9c10-fab2e63a4906"

        async def lca_side_effect(vehicle_id):
            if vehicle_id == ebus_id:
                return MOCK_LCA_IMPACT_EBUS
            if vehicle_id == diesel_cfg_id:
                return {"gwp100a": None, "nox": None}
            return None

        with patch(
            "app.routers.yearly_analysis._lca_get_impact",
            new_callable=AsyncMock,
            side_effect=lca_side_effect,
        ), patch(
            "app.routers.yearly_analysis._resolve_lca_vehicle",
            new_callable=AsyncMock,
        ) as mock_resolve:
            mock_ebus_lca = MagicMock()
            mock_ebus_lca.id = ebus_id
            mock_ebus_lca.passenger_capacity = 64
            mock_ebus_lca.functional_unit = "pkm"
            mock_resolve.return_value = mock_ebus_lca

            resp = client.get(
                f"/api/v1/yearly-analysis/{test_yearly_analysis['ya_id']}/emissions",
                params={"bus_length_m": 12},
                headers=headers,
            )
        assert resp.status_code == 200
        dc = resp.json()["lifecycle_breakdown"]["diesel_comparator"]
        assert dc["available"] is False
        assert dc["reason"] == "empty_phase_response"
