"""Rebuild a report case from a read-only JSON export, without DB writes."""
import argparse
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from app.routers.yearly_analysis import _build_energy_summary
from app.services.parameterized_lca import build_vehicles, evaluate_fleet, MobitoolClient, catalog


async def main(path):
    results = []
    for source in json.loads(Path(path).read_text()):
        runs = [SimpleNamespace(**r) for r in source["runs"]]
        analysis = SimpleNamespace(id=source["id"], features=source["features"])
        energy = await _build_energy_summary(analysis, runs)
        vehicles = build_vehicles(runs, energy, source["features"], source["models"])
        lca = await evaluate_fleet(vehicles, MobitoolClient(catalog()["base_url"]))
        results.append({"id": source["id"], "name": source["name"], "energy": energy, "lca": lca})
    print(json.dumps(results, default=str, allow_nan=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    asyncio.run(main(parser.parse_args().input))
