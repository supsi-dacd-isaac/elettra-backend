import os
import sys
import asyncio
import math
import re
import argparse
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / 'tests' / 'test.env'


def ensure_pythonpath() -> None:
    # Add project root to PYTHONPATH so we can import local packages if needed
    root_str = str(PROJECT_ROOT)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)


def load_environment() -> None:
    # Load env vars from tests/test.env (contains DATABASE_URL)
    if ENV_PATH.exists():
        load_dotenv(dotenv_path=str(ENV_PATH))


def get_database_url() -> str:
    db_url = os.getenv('DATABASE_URL')
    if not db_url:
        raise RuntimeError('DATABASE_URL not set. Ensure tests/test.env is loaded.')
    return db_url


def parse_gtfs_hms_to_seconds(time_str: Optional[str]) -> Optional[int]:
    if not time_str:
        return None
    parts = time_str.split(':')
    if len(parts) != 3:
        return None
    try:
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds = int(parts[2])
        if minutes < 0 or minutes > 59 or seconds < 0 or seconds > 59:
            return None
        return hours * 3600 + minutes * 60 + seconds
    except ValueError:
        return None


def minute_bucket(seconds: Optional[int]) -> Optional[int]:
    if seconds is None:
        return None
    return seconds // 60


def natural_sort_key(value: Optional[str]):
    s = value or ''
    tokens = re.findall(r'\d+|\D+', s)
    key = []
    for tok in tokens:
        if tok.isdigit():
            key.append((0, int(tok)))
        else:
            key.append((1, tok.casefold()))
    return key


async def create_engine_async() -> AsyncEngine:
    db_url = get_database_url()
    engine = create_async_engine(db_url, echo=False, pool_pre_ping=True)
    return engine


async def fetch_lugano_centro_stop_ids(engine: AsyncEngine) -> List[str]:
    sql = text(
        """
        SELECT id::text
        FROM gtfs_stops
        WHERE stop_name IN ('Lugano, Centro')
        """
    )
    async with engine.connect() as conn:
        result = await conn.execute(sql)
        rows = result.fetchall()
    return [r[0] for r in rows]


def _in_clause_params(ids: Iterable[str]) -> Tuple[str, dict]:
    ids_list = list(ids)
    if not ids_list:
        return '(NULL)', {}
    placeholders = []
    params = {}
    for i, stop_id in enumerate(ids_list):
        key = f'stop_id_{i}'
        placeholders.append(f':{key}')
        params[key] = stop_id
    return '(' + ','.join(placeholders) + ')', params


async def fetch_day_stop_times(
    engine: AsyncEngine,
    stop_ids: List[str],
    weekday_col: str,
    agency_id: Optional[str] = None,
) -> List[dict]:
    if not stop_ids:
        return []
    in_clause, params = _in_clause_params(stop_ids)
    # Note: weekday_col is interpolated in SQL identifier position. It is controlled internally.
    agency_filter = " AND r.agency_id = :agency_id " if agency_id else ""
    sql = text(
        f"""
        SELECT
            st.arrival_time,
            st.departure_time,
            r.route_short_name,
            r.route_long_name,
            r.route_id AS route_id_str
        FROM gtfs_stops_times st
        JOIN gtfs_trips t ON st.trip_id = t.id
        JOIN gtfs_calendar c ON t.service_id = c.id
        JOIN gtfs_routes r ON t.route_id = r.id
        WHERE st.stop_id IN {in_clause}
          AND t.status = 'gtfs'
          AND c.{weekday_col} = 1
          {agency_filter}
        """
    )
    async with engine.connect() as conn:
        if agency_id:
            params = {**params, 'agency_id': agency_id}
        result = await conn.execute(sql, params)
        rows = result.fetchall()
    data = []
    for row in rows:
        arrival, departure, short_name, long_name, rid = row
        route_label = short_name if (short_name is not None and str(short_name).strip() != '') else (long_name if long_name else str(rid))
        data.append(
            {
                'arrival_time': arrival,
                'departure_time': departure,
                'route_label': route_label,
            }
        )
    return data


def build_minute_dataframe(day_rows: List[dict]) -> pd.DataFrame:
    if not day_rows:
        return pd.DataFrame()

    # Determine route set and time bounds
    route_labels = set()
    min_minute = math.inf
    max_minute = -math.inf

    intervals: List[Tuple[str, int, int]] = []
    for rec in day_rows:
        arr_s = parse_gtfs_hms_to_seconds(rec['arrival_time'])
        dep_s = parse_gtfs_hms_to_seconds(rec['departure_time'])

        start_min = minute_bucket(arr_s) if arr_s is not None else minute_bucket(dep_s)
        end_min = minute_bucket(dep_s) if dep_s is not None else minute_bucket(arr_s)

        if start_min is None and end_min is None:
            continue
        if start_min is None:
            start_min = end_min
        if end_min is None:
            end_min = start_min

        # Occupied inclusive of departure minute
        if end_min < start_min:
            # Guard against bad data; swap
            start_min, end_min = end_min, start_min

        intervals.append((rec['route_label'], start_min, end_min))
        route_labels.add(rec['route_label'])
        min_minute = min(min_minute, start_min)
        max_minute = max(max_minute, end_min)

    if not intervals:
        return pd.DataFrame()

    # Normalize to start from 0 minutes of service day
    # Spec says y-axis should extend up to max observed; we keep index starting at 0
    num_minutes = max_minute + 1

    routes_sorted = sorted(route_labels, key=natural_sort_key)
    df = pd.DataFrame(0, index=pd.RangeIndex(0, num_minutes, name='minute'), columns=routes_sorted, dtype='int32')

    for route_label, start_min, end_min in intervals:
        # Increment all occupied minutes [start, end]
        df.loc[start_min:end_min, route_label] += 1

    return df


def plot_heatmap(df: pd.DataFrame, weekday_name: str, output_dir: Path, save_pdf: bool = True) -> Optional[Path]:
    if df.empty:
        return None

    # Try seaborn; fallback to matplotlib if unavailable
    try:
        import seaborn as sns  # type: ignore
    except Exception:  # pragma: no cover
        sns = None

    import matplotlib.pyplot as plt  # noqa: E402

    # Size heuristics
    num_minutes = df.shape[0]
    num_routes = df.shape[1]
    height = max(6.0, min(14.0, num_minutes / 120.0))  # ~2 hours per inch, capped
    width = max(6.0, min(24.0, num_routes * 0.4))

    # Create two subplots: left heatmap, right totals line; share Y axis (minutes)
    fig, (ax_hm, ax_line) = plt.subplots(
        ncols=2,
        figsize=(width + 4, height),
        gridspec_kw={'width_ratios': [max(1, num_routes / 5), 1]},
        sharey=True,
    )

    data = df  # rows: minutes (y), cols: routes (x)

    if sns is not None:
        hm = sns.heatmap(
            data,
            ax=ax_hm,
            cmap='magma',
            cbar=True,
            cbar_kws={'label': 'buses at stop'},
            linewidths=0,
            square=False,
        )
    else:
        im = ax_hm.imshow(data.values, aspect='auto', origin='upper', cmap='magma', interpolation='nearest')
        cbar = fig.colorbar(im, ax=ax_hm)
        cbar.set_label('buses at stop')

    ax_hm.set_title(f"Lugano, Centro — {weekday_name}")
    ax_hm.set_xlabel('Route short name')
    ax_hm.set_ylabel('Time of service day')

    # X ticks: show all routes or a subset if too many
    routes = list(df.columns)
    ax_hm.set_xticks(range(len(routes)))
    ax_hm.set_xticklabels(routes, rotation=90)

    # Y ticks: hourly markers
    max_minute = df.index.max()
    hour_ticks = list(range(0, max_minute + 1, 60))
    hour_labels = [f"{m // 60:02d}:{m % 60:02d}" for m in hour_ticks]
    ax_hm.set_yticks(hour_ticks)
    ax_hm.set_yticklabels(hour_labels)

    # Right subplot: total buses per minute (sum across routes)
    totals = df.sum(axis=1)
    ax_line.plot(totals.values, df.index.values, color='tab:blue', linewidth=1.2)
    ax_line.set_xlabel('Total buses')
    ax_line.grid(True, axis='x', linestyle=':', alpha=0.5)
    # Align y ticks with left plot
    ax_line.set_yticks(hour_ticks)
    ax_line.set_yticklabels([])
    ax_line.invert_yaxis()  # keep origin at top to match heatmap

    fig.tight_layout()

    output_dir.mkdir(parents=True, exist_ok=True)
    outfile = output_dir / f'lugano_centro_heatmap_{weekday_name.lower()}.png'
    fig.savefig(outfile, dpi=200)
    if save_pdf:
        outfile_pdf = output_dir / f'lugano_centro_heatmap_{weekday_name.lower()}.pdf'
        fig.savefig(outfile_pdf)
    plt.close(fig)
    return outfile


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Generate heatmaps for Lugano, Centro stop')
    parser.add_argument('--agency-id', type=str, default=None, help='Optional agency UUID to filter routes/trips')
    return parser.parse_args()


async def main() -> None:
    ensure_pythonpath()
    load_environment()
    engine = await create_engine_async()

    args = parse_args()
    agency_id = args.agency_id

    stop_ids = await fetch_lugano_centro_stop_ids(engine)
    if not stop_ids:
        print('No stops found with stop_name IN (\'Lugano, Centro\'). Exiting.')
        return

    weekday_map = [
        ('monday', 'Monday'),
        ('tuesday', 'Tuesday'),
        ('wednesday', 'Wednesday'),
        ('thursday', 'Thursday'),
        ('friday', 'Friday'),
        ('saturday', 'Saturday'),
        ('sunday', 'Sunday'),
    ]

    output_dir = PROJECT_ROOT / 'playground'

    for weekday_col, weekday_name in weekday_map:
        rows = await fetch_day_stop_times(engine, stop_ids, weekday_col, agency_id=agency_id)
        df = build_minute_dataframe(rows)
        path = plot_heatmap(df, weekday_name, output_dir, save_pdf=True)
        if path:
            print(f'Saved {weekday_name}: {path}')
        else:
            print(f'No data for {weekday_name}.')

    await engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())


