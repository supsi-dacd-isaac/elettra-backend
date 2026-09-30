"""Read-only acceptance probe against the frozen Mobitool upstream.

Print evidence as JSON; never mutates a database or an upstream resource.
Run with the repository's Python environment and PYTHONPATH=.
"""
import asyncio
import json
from app.services.parameterized_lca import MobitoolClient, catalog, evaluate_fleet


async def main():
    vehicles = []
    for length in (9, 12, 18):
        vehicles.append(dict(shift_id=f"acceptance-{length}", bus_length_m=length,
            annual_km=60000, dc_kwh=90000, battery_capacity_kwh=400,
            battery_chemistry="NMC", lifetime_bus=12, lifetime_battery=8,
            lifetime_diesel_bus=10, passengers=20, electricity_mix="CONSUMER_PHYSICAL",
            diesel_consumption_l_per_km=.02*length+.1918, diesel_heating_liters=500))
    variants = [
        {"battery_capacity_kwh": 600}, {"battery_chemistry": "LFP"},
        {"battery_chemistry": "LTO"}, {"lifetime_battery": 4},
        {"lifetime_bus": 16}, {"annual_km": 90000, "dc_kwh": 135000, "diesel_heating_liters": 750},
        {"passengers": 0},
    ]
    vehicles.extend({**vehicles[1], **changes, "shift_id": f"variant-{i}"} for i, changes in enumerate(variants))
    result = await evaluate_fleet(vehicles, MobitoolClient(catalog()["base_url"]))
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    asyncio.run(main())
