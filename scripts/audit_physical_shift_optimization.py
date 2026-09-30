"""Read-only historical audit and pure-solver replay of explicitly named runs.

Run with the deployment's release pins and a SOURCE_DATABASE_URL. No database
writes or prediction generation are permitted; incomplete cases are reported.
Results go into a new, user-specified output directory, never historical rows.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sys
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.models import OptimizationRuns, PredictionRuns
from app.services.optimization import prepare_optimization_input
from simulation.optimization_contract import pack_count, result_integrity, select_references
from simulation.optimization_verification import optimize_and_verify


async def main(args):
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    engine = create_async_engine(os.environ["SOURCE_DATABASE_URL"], poolclass=NullPool,
                                 connect_args={"server_settings": {"default_transaction_read_only": "on"}})
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with sessions() as db:
        await db.execute(text("SET TRANSACTION READ ONLY"))
        runs = list((await db.execute(select(OptimizationRuns))).scalars().all())
        audit = [{"id": str(r.id), "name": r.name, "mode": r.mode, "run_status": r.status,
                  **result_integrity(r.results, (r.input_params or {}).get("shift_ids"))} for r in runs]
        (output / "historical_audit.json").write_text(json.dumps(audit, indent=2))
        for rid in args.replay:
            run = next(r for r in runs if str(r.id) == rid)
            record = {"original_run_id": rid, "name": run.name, "original_results": run.results}
            try:
                ids = [UUID(pid) for pid in run.prediction_run_ids or []]
                predictions = [await db.get(PredictionRuns, pid) for pid in ids]
                if any(pred is None for pred in predictions):
                    raise ValueError("Original prediction catalogue is incomplete")
                explicit = None
                if run.mode == "charging_only":
                    # Replays require an explicit CLI battery choice, never an
                    # inferred historical choice from an ambiguous saved run.
                    if args.charging_packs is None:
                        raise ValueError("Specify --charging-packs for charging-only replay")
                    explicit = {}
                    for sid in run.input_params["shift_ids"]:
                        options = [p for p in predictions if str(p.shift_id) == sid and pack_count(p) == args.charging_packs]
                        if not options:
                            raise ValueError("Requested fixed-battery forecast is unavailable")
                        explicit[sid] = str(max(options, key=lambda p: str(p.id)).id)
                refs = select_references(predictions, run.input_params["shift_ids"], mode=run.mode,
                                         user_id=run.user_id, explicit=explicit)
                ref_ids = {sid: str(p.id) for sid, p in refs.items()}
                buses, stations, config = await prepare_optimization_input(db, run, ids, reference_ids=ref_ids)

                async def exact(packs):
                    exact_ids = {}
                    for sid, count in packs.items():
                        options = [p for p in predictions if str(p.shift_id) == sid and pack_count(p) == count]
                        if not options:
                            raise ValueError(f"No existing full forecast at {count} packs for {sid}; no generation in audit")
                        exact_ids[sid] = str(max(options, key=lambda p: str(p.id)).id)
                    exact_buses, _, _ = await prepare_optimization_input(
                        db, run, [UUID(p) for p in exact_ids.values()], reference_ids=exact_ids)
                    return exact_buses, exact_ids

                result, verification = await optimize_and_verify(buses, stations, config, exact,
                    time_budget_seconds=args.time_budget)
                record.update(result=asdict(result), verification=verification, initial_references=ref_ids)
            except Exception as exc:
                record["replay_error"] = str(exc)
            (output / f"{rid}.json").write_text(json.dumps(record, indent=2, default=str, allow_nan=False))
            print(json.dumps({"id": rid, "error": record.get("replay_error"),
                              "verification": record.get("verification", {}).get("status")}), flush=True)
        await db.rollback()
    await engine.dispose()
    checksums = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.iterdir()) if p.is_file()}
    (output / "manifest.json").write_text(json.dumps({"database_access": "read_only", "sha256": checksums}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--replay", action="append", default=[])
    parser.add_argument("--charging-packs", type=int)
    parser.add_argument("--time-budget", type=int, default=900)
    asyncio.run(main(parser.parse_args()))
