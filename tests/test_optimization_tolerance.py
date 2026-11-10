"""Runtime default, request precedence and two-phase solver regression checks."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

from app.core import config as settings_module
from app.services import optimization as service
from simulation.optimization_model import OptimizationConfig, solve_optimization
from simulation.optimization_verification import optimize_and_verify
from test_optimization_physical_shift import bus, station

ENV = "ELETTRA_OPTIMIZATION_MIP_REL_GAP"


@pytest.fixture
def runtime_settings(monkeypatch, tmp_path):
    baseline = yaml.safe_load(open("config/elettra-config.image.yaml"))
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(baseline))
    monkeypatch.setenv("ELETTRA_CONFIG_FILE", str(path))
    monkeypatch.chdir(tmp_path)  # Ignore developer .env files.
    monkeypatch.delenv(ENV, raising=False)
    return settings_module.get_settings


def test_default_gap_is_one_millionth(runtime_settings):
    assert runtime_settings().optimization_mip_rel_gap == 1e-6


@pytest.mark.parametrize("raw,expected", [("0", 0), ("1e-8", 1e-8),
    ("1e-6", 1e-6), ("0.001", .001), ("1", 1), (" 1e-4 ", 1e-4)])
def test_environment_configures_gap(runtime_settings, monkeypatch, raw, expected):
    monkeypatch.setenv(ENV, raw)
    assert runtime_settings().optimization_mip_rel_gap == expected


@pytest.mark.parametrize("raw", ["-1e-6", "1.01", "NaN", "inf", "-inf", "invalid", ""])
def test_invalid_environment_refuses_startup(runtime_settings, monkeypatch, raw):
    monkeypatch.setenv(ENV, raw)
    with pytest.raises(ValidationError):
        runtime_settings()


@pytest.mark.parametrize("params,expected", [({}, 1e-4), ({"mip_rel_gap": None}, 1e-4),
    ({"mip_rel_gap": 0}, 0), ({"mip_rel_gap": 1e-8}, 1e-8),
    ({"mip_rel_gap": 1e-3}, 1e-3)])
def test_explicit_request_precedence(monkeypatch, params, expected):
    monkeypatch.setattr(service, "get_cached_settings", lambda: SimpleNamespace(
        optimization_mip_rel_gap=1e-4))
    original = dict(params)
    assert service.effective_mip_rel_gap(params) == expected
    assert params == original


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["battery_only", "charging_only", "joint"])
@pytest.mark.parametrize("gap", [0, 1e-6, 1e-3])
async def test_both_solver_phases_keep_gap_and_physical_limits(monkeypatch, mode, gap):
    monkeypatch.setattr(service, "get_cached_settings", lambda: SimpleNamespace(
        optimization_mip_rel_gap=gap))
    vehicle = bus(energy=60)
    config = OptimizationConfig(mode=mode, min_soc=.1, max_soc=.9,
        battery_cost_per_kwh=100, mip_rel_gap=service.effective_mip_rel_gap({}),
        mip_abs_gap=0, feasibility_tol=1e-7, optimality_tol=1e-7)
    calls = []

    def solve(buses, stations, options):
        calls.append(options)
        return solve_optimization(buses, stations, options)

    async def exact(packs):
        return [replace(vehicle, reference_packs=packs[vehicle.shift_id])], {}

    result, verification = await optimize_and_verify(
        [vehicle], [station()], config, exact, solve=solve)
    assert verification["status"] == "verified"
    assert result.electrification_feasible
    assert len(calls) == 2
    assert calls[0].fixed_battery_packs is None
    assert calls[1].fixed_battery_packs is not None
    for options in calls:
        assert options.mip_rel_gap == gap
        assert options.mip_abs_gap == 0
        assert (options.min_soc, options.max_soc) == (.1, .9)
        assert options.feasibility_tol == options.optimality_tol == 1e-7
    assert calls[1].allow_excess_packs is False
