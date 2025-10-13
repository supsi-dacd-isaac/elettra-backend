## Playground scripts: Excel ➜ CSV ➜ Shift JSON

This folder contains two helper scripts that convert scheduling spreadsheets into CSV and then reconstruct per-shift JSON using the Elettra backend API.

### Prerequisites
- Python 3.10+
- Recommended: virtualenv
- Project requirements installed
- Elettra backend running and reachable (default `http://localhost:8002`)

Setup example:
```bash
cd /home/elettra/projects/elettra-backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Start backend (compose build first if you changed code)
docker-compose up -d

# Load test creds for API login used by the JSON builder
source tests/test.env
```

---

## 1) build_shift_csv_from_excel.py

Convert a TM Excel file into a normalized CSV with the following columns:
- `Tipologia veicolo:` (copied from Excel header if present)
- `Linea` (line code extracted from "Linea: <code>" blocks)
- `Stop` (stop name)
- `Partenza` (HH:MM; ordering is chronological with midnight wrap)

Assumptions about the Excel layout (based on observed sheets):
- A header area contains `Tipologia veicolo:` and the vehicle type to the right.
- Trip blocks are headed by cells like `Linea: <code>`.
- Within blocks, times appear next to markers `Da`/`A`; the script backtracks left to find the stop name.
- The final depot row (e.g., `TPL Rimessa 1`) is appended when detected.

Usage:
```bash
python playground/build_shift_csv_from_excel.py /abs/path/to/00_40101.xlsx

# Optional explicit output path
python playground/build_shift_csv_from_excel.py /abs/path/to/00_40101.xlsx --out /abs/path/to/out/00_40101.csv
```

Output location (when --out is omitted):
- Creates a sibling folder named like `<excel_folder>_csv` and writes `<stem>.csv` there.

CSV format details:
- Separator: `;`
- Columns: `Tipologia veicolo:;Linea;Stop;Partenza`

---

## 2) build_shift_json_from_csv.py

Reconstruct one or more shift JSON files from CSV produced by the script above, resolving each service segment to GTFS and depot trips through the backend API.

Two modes:
- Single file: `--csv <file.csv> [--output <file.json>]`
- Batch folder: `--input-dir <folder_with_csvs>` (writes JSONs to a derived `...json` folder)

CLI options (key flags):
- `--csv` or `--input-dir` (mutually exclusive, one required)
- `--output` output path for single-file mode; batch mode writes next to each CSV inside an output folder
- `--agency-id` default `ec5a38c2-e6af-461b-a0a9-0ab70b82cf7b`
- `--route-short-name` default `1` (can be inferred from filename like `00_40101.csv`)
- `--base-url` default `http://localhost:8002`
- `--env` path to env file providing `TEST_LOGIN_EMAIL` and `TEST_LOGIN_PASSWORD` (default points to `tests/test.env`)
- `--day-of-week` one of `mon-fri|sat|sun` (default `mon-fri`)
- `--fuzzy-stops` enable fuzzy stop-name matching with warnings
- `--time-tolerance-min` allow ±N minutes when matching times (default `0`)

Authentication:
- The script logs into the backend using credentials from `--env` or the environment.
- Ensure `TEST_LOGIN_EMAIL` and `TEST_LOGIN_PASSWORD` are set (e.g., by `source tests/test.env`).

Examples
```bash
# Single CSV ➜ JSON (route inferred from filename if possible)
python playground/build_shift_json_from_csv.py \
  --csv /home/elettra/data/TM_csv/00_40101.csv \
  --output /home/elettra/data/TM_json/shift_40101_reconstructed.json \
  --day-of-week sat \
  --time-tolerance-min 2 \
  --fuzzy-stops

# Batch folder ➜ JSON folder (auto-derives output dir name by replacing 'csv' with 'json')
python playground/build_shift_json_from_csv.py \
  --input-dir /home/elettra/data/2026-TM_6f_Sa_TM_csv \
  --day-of-week sat
```

Outputs
- Single-file mode writes one JSON; if the CSV holds multiple shifts, files like `...__part1.json`, `...__part2.json` are created.
- Batch mode writes one JSON per CSV into the derived output folder and reports per-file success/failure.

Input expectations
- CSV produced by `build_shift_csv_from_excel.py` with columns `Tipologia veicolo:;Linea;Stop;Partenza`.
- Times may wrap past midnight (e.g., `24:10` handled by the script).

Troubleshooting
- Auth errors: verify backend is up, `--base-url`, and credentials via `tests/test.env` or env vars.
- "Route not found": check `--route-short-name` or let the script infer from filename.
- "No GTFS trip found": try `--fuzzy-stops` and/or `--time-tolerance-min 1..3`, confirm `--day-of-week`.

---

### Notes
- The JSON builder calls Elettra endpoints: auth, routes by agency, trips by route (GTFS and depot).
- Default ports: Backend `8002`. Ensure services are healthy before running batch conversions.


