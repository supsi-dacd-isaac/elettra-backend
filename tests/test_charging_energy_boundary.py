import ast
from pathlib import Path
import pytest
from app.services.charging_energy import energy_boundary, grid_from_dc
from app.services.charging_energy import connection_estimate, station_connections


def test_conversion_only_at_grid_boundary():
    assert grid_from_dc(94) == 100
    assert grid_from_dc(150) == pytest.approx(159.5744680851064)
    assert energy_boundary(94)["charging_losses_kwh"] == 6
    assert not energy_boundary(94)["preconditioning_included"]


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf")])
def test_invalid_aggregate(value):
    with pytest.raises(ValueError):
        grid_from_dc(value)


def test_optimizer_has_no_grid_conversion_dependency():
    # This policy is reporting-only. SOC dynamics must retain DC power * dt.
    path = Path(__file__).resolve().parents[1] / "simulation/optimization_model.py"
    tree = ast.parse(path.read_text())
    assert not any(isinstance(n, ast.ImportFrom) and "charging_energy" in (n.module or "") for n in ast.walk(tree))
    func = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "soc_dyn_rule")
    assert "0.94" not in ast.unparse(func)
    assert "float(dt) * mdl.power[t, b]" in ast.unparse(func)


@pytest.mark.asyncio
async def test_electric_energy_endpoint_uses_grid_price():
    from app.routers.economic import get_electric_energy_cost
    response = await get_electric_energy_cost(annual_consumption_kwh=94, energy_price_per_kwh=.2, current_user=None)
    assert response.cost_per_year_chf == 20
    assert response.annual_consumption_kwh == 94
    assert response.energy_boundary["grid_kwh"] == 100


def test_ac_power_not_converted_twice_and_shared_station_counted_once():
    assert connection_estimate(94, supplied_ac_kw=100)["ac_connection_power_kw"] == 100
    stations = [{"stop_id": "shared", "max_total_power_kw": 300,
                 "max_power_per_slot_kw": 150, "num_slots": 2}]
    estimates = station_connections(stations, {"shared": {"num_slots": 2}})
    assert len(estimates) == 1
    assert estimates[0]["ac_connection_power_kw"] == pytest.approx(300/.94)
    assert not estimates[0]["ac_limit_verified_by_optimizer"]


def test_charged_energy_is_not_the_duty_consumption():
    # A day consuming 180 kWh and charging 150 kWh ends 30 kWh below its initial SOC.
    charged = energy_boundary(150)
    annual_replenishment_per_day = energy_boundary(180)
    assert charged["grid_kwh"] == pytest.approx(159.5744680851)
    assert annual_replenishment_per_day["grid_kwh"] == pytest.approx(191.4893617021)
