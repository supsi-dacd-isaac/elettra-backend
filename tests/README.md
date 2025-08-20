# Test Suite

This directory contains the automated test suite for the Elettra Backend.

## Structure
```
tests/
  README.md                (this file)
  conftest.py              (shared fixtures, report generation)
  auth/
    test_auth.py           (authentication & security perimeter tests)
  reports/                 (timestamped text + JSON reports)
```

## Available Test Groups
- **auth**: Validates authentication endpoints (`/auth/login`, `/auth/register`, `/auth/me`), token handling, malformed headers, basic security (SQL injection attempts), OpenAPI exposure, and access-control responses.

## Fixtures & Utilities
- `client`: Synchronous `TestClient` for straightforward endpoint testing.
- `async_client`: Async `httpx.AsyncClient` (use only if writing explicit async tests).
- `record`: Helper to assert and log test outcome while feeding structured data into the report system.
- Automatic timing: Each test duration is recorded.
- Reports: After each module run a verbose text and JSON report is written to `tests/reports/`.

## Running Tests
Basic full run:
```
pytest
```
Auth tests only:
```
pytest -k auth
```
Specific file:
```
pytest tests/auth/test_auth.py
```
Show all test names & reasons for skips (none currently):
```
pytest -vv -rA
```

## Colored Reports (optional)
Reports are plain text by default. To embed ANSI colors in the saved report files:
```
TEST_REPORT_COLOR=1 pytest -k auth
```
(Leave variable unset for plain text.)

## Coverage (optional)
```
pytest --cov=app --cov-report=term-missing --cov-report=html
# Open htmlcov/index.html in a browser for detailed annotated coverage
```

## Reports
Each module generates two files named like:
```
reports/auth_YYYYMMDD_HHMMSS.txt
reports/auth_YYYYMMDD_HHMMSS.json
```
Text report: human readable, includes timing, status, summary.
JSON report: machine readable (can be parsed by CI or dashboards).

## Adding New Test Modules
1. Create a subfolder matching the domain (e.g., `gtfs/`, `routes/`).
2. Name files `test_*.py` so pytest auto-discovers them.
3. Set `__report_module__ = "<short-name>"` at the top *if you want a custom report prefix* (defaults to Python module name). Example:
   ```python
   __report_module__ = "routes"
   ```
4. Use the `record` fixture for assertions to ensure inclusion in the report:
   ```python
   def test_example(client, record):
       r = client.get("/api/v1/gtfs-routes/")
       record("list_routes", r.status_code == 200, f"status={r.status_code}")
   ```
5. Avoid long-lived global state; rely on fixtures.

## Writing Assertions
Prefer a single logical condition per `record` call so the report is clear. If multiple phases are needed, split into separate tests or multiple `record` calls (each creates its own entry & assertion).

## Environment Variables Influencing Tests
| Variable            | Purpose                                     |
|---------------------|---------------------------------------------|
| `ELETTRA_CONFIG_FILE` | Path to configuration YAML/JSON (app startup) |
| `TEST_REPORT_COLOR` | Enable color codes in saved text reports     |

## Common Warnings (Expected)
Some third-party deprecation warnings may appear (e.g., `passlib`, `starlette`, `python-jose`). They are upstream and do not currently break functionality.

## Troubleshooting
| Symptom                                      | Fix |
|----------------------------------------------|-----|
| `ModuleNotFoundError: main`                  | Ensure project root is on `PYTHONPATH` (handled in `conftest.py`). |
| Async event loop / `another operation in progress` errors | Use the sync `client` unless async semantics are required. |
| No report files produced                     | Confirm at least one test used `record` fixture. |

## Roadmap / Future Enhancements
- Add fixtures for test data seeding & teardown.
- Introduce a separate test database or schema sandbox.
- Expand security tests (rate limiting, brute-force heuristics, JWT tampering).
- Integration tests for GTFS ingestion pipeline.

## Minimal Template for a New Test File
```python
__report_module__ = "sample"

def test_sample_health(client, record):
    r = client.get("/")
    record("root_health", r.status_code == 200, f"status={r.status_code}")
```

---
Maintain consistency; keep tests fast, deterministic, and isolated.

