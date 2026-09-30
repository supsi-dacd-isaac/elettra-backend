"""Seed only the isolated grid94 release database from a private read-only export.

Usage inside the staging API container: python /tmp/seed.py /tmp/cases.json
Never accepts a production database URL.
"""
import asyncio
import json
import os
import sys
from pathlib import Path
from sqlalchemy import text
from app.database import engine


async def main():
    if os.environ.get("DATABASE_URL") != "postgresql+asyncpg://admin:grid94-isolated-staging@db:5432/elettra":
        raise RuntimeError("This seed is restricted to the isolated grid94 Compose project")
    owner = "22222222-2222-4222-8222-222222222222"
    sources = json.loads(Path(sys.argv[1]).read_text())
    async with engine.begin() as db:
        for source in sources:
            for mid, specs in source["models"].items():
                await db.execute(text("INSERT INTO buses_models(id,name,user_id,specs) VALUES(:id,'Grid94 frozen report vehicle',:owner,CAST(:specs AS jsonb)) ON CONFLICT DO NOTHING"), {"id":mid,"owner":owner,"specs":json.dumps(specs)})
            await db.execute(text("INSERT INTO yearly_analysis(id,name,features) VALUES(:id,:name,CAST(:features AS jsonb)) ON CONFLICT DO NOTHING"), {"id":source["id"],"name":source["name"],"features":json.dumps(source["features"])})
            for r in source["runs"]:
                await db.execute(text("INSERT INTO shifts(id,name) VALUES(:id,'Grid94 frozen duty') ON CONFLICT DO NOTHING"), {"id":r["shift_id"]})
                values = {**r,"owner":owner,"yearly_analysis_id":source["id"],"summary":json.dumps(r["summary"]),"contextual_parameters":json.dumps(r["contextual_parameters"])}
                await db.execute(text("""INSERT INTO prediction_runs(id,user_id,shift_id,bus_model_id,yearly_analysis_id,model_name,prediction_stack,external_temp_celsius,auxiliary_heating_type,contextual_parameters,summary,status)
                VALUES(:id,:owner,:shift_id,:bus_model_id,:yearly_analysis_id,:model_name,:prediction_stack,:external_temp_celsius,:auxiliary_heating_type,CAST(:contextual_parameters AS jsonb),CAST(:summary AS jsonb),:status) ON CONFLICT DO NOTHING"""), values)
    print(f"Seeded {len(sources)} frozen cases into isolated staging")


if __name__ == "__main__":
    asyncio.run(main())
