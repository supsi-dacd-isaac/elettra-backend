#!/usr/bin/env python3
"""Reproduce or verify the VECTO 5.1.3 SSM golden fixture safely.

Verification is read-only by default. ``--update-golden`` is the only mode that
writes the checked-in oracle output, and all binary hashes/version checks must
pass before it can do so.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
CASES = FIXTURES / "vecto_ssm_5_1_3_cases.json"
GOLDEN = FIXTURES / "vecto_ssm_5_1_3_golden.json"
PROVENANCE = FIXTURES / "vecto_ssm_5_1_3_provenance.json"
ORACLE_DIR = ROOT / "tests" / "vecto_oracle"
DEFAULT_IMAGE = "elettra-vecto-oracle:5.1.3"

RESULT_FIELDS = (
    "electrical_cooling_and_ventilation_w",
    "mechanical_cooling_w",
    "required_heating_power_w",
    "electrical_heat_pump_w",
    "mechanical_heat_pump_w",
    "electric_heater_w",
    "fuel_heater_w",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(command), file=sys.stderr)
    return subprocess.run(command, text=True, check=True, **kwargs)


def _validate_distribution(vecto_bin: Path, provenance: dict) -> None:
    expected_hashes = {
        "VectoCore.dll": provenance["vecto_core_dll_sha256"],
        "VectoCommon.dll": provenance["vecto_common_dll_sha256"],
        "vectocmd.dll": provenance["vectocmd_dll_sha256"],
    }
    for filename, expected in expected_hashes.items():
        path = vecto_bin / filename
        if not path.is_file():
            raise FileNotFoundError(f"missing official VECTO binary: {path}")
        actual = _sha256(path)
        if actual != expected:
            raise RuntimeError(
                f"{filename} SHA-256 mismatch: expected {expected}, got {actual}"
            )

    version = _run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{vecto_bin.resolve()}:/vecto:ro",
            "mcr.microsoft.com/dotnet/runtime:8.0",
            "dotnet",
            "/vecto/vectocmd.dll",
            "-h",
        ],
        capture_output=True,
    ).stdout
    expected_console = f"VectoConsole: {provenance['vecto_console_version']}"
    expected_core = f"VectoCore: {provenance['vecto_core_version']}"
    if expected_console not in version or expected_core not in version:
        raise RuntimeError(
            "unexpected VECTO version output; expected "
            f"{expected_console!r} and {expected_core!r}, got:\n{version}"
        )


def _official_results(vecto_bin: Path, image: str) -> list[dict]:
    _run(
        [
            "docker",
            "build",
            "--build-context",
            f"vecto={vecto_bin.resolve()}",
            "-t",
            image,
            str(ORACLE_DIR),
        ]
    )
    completed = _run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{FIXTURES.resolve()}:/cases:ro",
            image,
            f"/cases/{CASES.name}",
        ],
        capture_output=True,
    )
    return json.loads(completed.stdout)


def _compare(expected: list[dict], actual: list[dict], tolerance_w: float) -> float:
    expected_names = [item["name"] for item in expected]
    actual_names = [item["name"] for item in actual]
    if expected_names != actual_names:
        raise RuntimeError(
            f"oracle case order/name mismatch: expected {expected_names}, got {actual_names}"
        )

    max_error = 0.0
    max_location = ""
    for expected_item, actual_item in zip(expected, actual, strict=True):
        for field in RESULT_FIELDS:
            error = abs(float(expected_item[field]) - float(actual_item[field]))
            if error > max_error:
                max_error = error
                max_location = f"{expected_item['name']}.{field}"
    if max_error > tolerance_w:
        raise RuntimeError(
            f"golden mismatch: maximum error {max_error:.12g} W at {max_location} "
            f"exceeds {tolerance_w:.12g} W"
        )
    return max_error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--vecto-bin",
        type=Path,
        default=Path(
            os.environ.get(
                "VECTO_BIN_DIR",
                str(ROOT / "tools" / "vecto-bin" / "net80"),
            )
        ),
        help="official VECTO 5.1.3 net80 directory",
    )
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--tolerance-w", type=float, default=1e-9)
    parser.add_argument(
        "--update-golden",
        action="store_true",
        help="explicitly overwrite the golden fixture with official output",
    )
    args = parser.parse_args()
    if args.tolerance_w < 0:
        parser.error("--tolerance-w must be non-negative")

    provenance = json.loads(PROVENANCE.read_text())
    _validate_distribution(args.vecto_bin, provenance)
    actual = _official_results(args.vecto_bin, args.image)
    if len(actual) != provenance["oracle_case_count"]:
        raise RuntimeError(
            f"expected {provenance['oracle_case_count']} oracle cases, got {len(actual)}"
        )

    if args.update_golden:
        # Write-and-replace prevents a partial golden file if the process dies.
        payload = json.dumps(actual, indent=2, allow_nan=False) + "\n"
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=GOLDEN.parent,
            prefix=f".{GOLDEN.name}.",
            delete=False,
        ) as stream:
            stream.write(payload)
            temporary = Path(stream.name)
        temporary.replace(GOLDEN)
        print(f"updated {GOLDEN}")
        return 0

    expected = json.loads(GOLDEN.read_text())
    max_error = _compare(expected, actual, args.tolerance_w)
    print(
        f"verified {len(actual)} VECTO 5.1.3 cases; "
        f"maximum error {max_error:.12g} W"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
