import os
import sys
from pathlib import Path
import pytest
from httpx import AsyncClient, ASGITransport
from datetime import datetime, timezone
from fastapi.testclient import TestClient
import platform
import json
import time

# Ensure project root on sys.path so `import main` works when pytest changes CWD
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Load environment variables from .env file if it exists

def load_env_file(env_path: Path):
    """Load environment variables from .env file"""
    if env_path.exists():
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    # Allow optional leading 'export '
                    if key.startswith('export '):
                        key = key[len('export '):]
                    # Remove quotes if present
                    value = value.strip('"\'')
                    os.environ[key.strip()] = value

# Try to load .env files in order of preference
env_files = [
    PROJECT_ROOT / ".env",
    PROJECT_ROOT / "tests" / "test.env",
    PROJECT_ROOT / ".env.test"
]

for env_file in env_files:
    if env_file.exists():
        load_env_file(env_file)
        break

from main import app  # noqa: E402
from app.core.config import get_cached_settings  # noqa: E402
from app.database import get_async_session  # noqa: E402

settings = get_cached_settings()

# ANSI color helpers
GREEN = "\x1b[32m"
RED = "\x1b[31m"
YELLOW = "\x1b[33m"
CYAN = "\x1b[36m"
BOLD = "\x1b[1m"
RESET = "\x1b[0m"
# Only emit ANSI in report files if user explicitly opts in via TEST_REPORT_COLOR=1
USE_COLOR = os.getenv("TEST_REPORT_COLOR") == "1"

# -----------------------------
# Sync TestClient fixture (session scope)
# -----------------------------
@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c

# -----------------------------
# Async HTTP client fixture (function scope to avoid event loop scope issues)
# -----------------------------
@pytest.fixture()
async def async_client():
    # Use explicit transport to avoid httpx deprecation warning about 'app' shortcut
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

# -----------------------------
# Raw DB session (function scope)
# -----------------------------
@pytest.fixture()
async def db_session():
    async for session in get_async_session():
        yield session
        break

# Timing fixture (autouse)
@pytest.fixture(autouse=True)
def _timer(request):
    start = time.perf_counter()
    yield
    duration_ms = (time.perf_counter() - start) * 1000.0
    # attach duration to node for later retrieval by record()
    setattr(request.node, "_duration_ms", duration_ms)

# -----------------------------
# Report collector
# -----------------------------
@pytest.fixture(scope="module")
def report_collector(request):
    results = []
    yield results
    os.makedirs("tests/reports", exist_ok=True)
    module_name = getattr(request.module, "__report_module__", request.module.__name__)
    # Use timezone-aware UTC timestamp
    now_utc = datetime.now(timezone.utc)
    timestamp = now_utc.strftime("%Y%m%d_%H%M%S")
    txt_path = f"tests/reports/{module_name}_{timestamp}.txt"
    json_path = f"tests/reports/{module_name}_{timestamp}.json"

    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed
    success_rate = (passed / total * 100.0) if total else 0.0

    # Write verbose text report
    with open(txt_path, "w", encoding="utf-8") as f:
        hdr = lambda s: f"{BOLD}{CYAN}{s}{RESET}" if USE_COLOR else s
        ok = lambda: f"{GREEN}PASS{RESET}" if USE_COLOR else "PASS"
        fail = lambda: f"{RED}FAIL{RESET}" if USE_COLOR else "FAIL"
        f.write(hdr(f"Verbose Report for {module_name} tests") + "\n")
        f.write(f"Generated (UTC): {now_utc.isoformat()}\n")
        f.write(f"App: {settings.app_name} v{settings.app_version}\n")
        f.write(f"Environment: Python {platform.python_version()} | Platform {platform.platform()}\n")
        f.write(f"Database URL (sanitized): {settings.database_url.split('@')[-1].rsplit(':',1)[0]}\n")
        summary_line = f"Total: {total}  Passed: {passed}  Failed: {failed}  Success: {success_rate:.1f}%"
        if USE_COLOR:
            if failed:
                summary_line = summary_line.replace(f"Failed: {failed}", f"Failed: {RED}{failed}{RESET}")
            summary_line = summary_line.replace(f"Passed: {passed}", f"Passed: {GREEN}{passed}{RESET}")
        f.write(summary_line + "\n")
        f.write(("="*80) + "\n\n")
        for idx, r in enumerate(results, start=1):
            status_colored = ok() if r['passed'] else fail()
            f.write(f"[{idx:02d}] Test: {r['name']}\n")
            f.write(f"     Status : {status_colored}\n")
            if r.get('duration_ms') is not None:
                f.write(f"     Duration: {r['duration_ms']:.2f} ms\n")
            if r['details']:
                f.write(f"     Details : {r['details']}\n")
            f.write("-"*80 + "\n")
        if failed:
            f.write("\nFailed Tests Summary:\n")
            for r in results:
                if not r['passed']:
                    f.write(f" - {r['name']}: {r['details']}\n")

    # Write JSON structured report
    with open(json_path, "w", encoding="utf-8") as jf:
        json.dump({
            "module": module_name,
            "generated_utc": now_utc.isoformat(),
            "app": {"name": settings.app_name, "version": settings.app_version},
            "environment": {
                "python": platform.python_version(),
                "platform": platform.platform()
            },
            "summary": {
                "total": total,
                "passed": passed,
                "failed": failed,
                "success_rate": success_rate
            },
            "results": results
        }, jf, indent=2)

# -----------------------------
# Helper to append results easily
# -----------------------------
@pytest.fixture
def record(report_collector, request):
    def _rec(name: str, passed: bool, details: str = ""):
        duration_ms = getattr(request.node, "_duration_ms", None)
        report_collector.append({
            "name": name,
            "passed": passed,
            "details": details,
            "duration_ms": duration_ms
        })
        assert passed, details  # Fail test if not passed
    return _rec
