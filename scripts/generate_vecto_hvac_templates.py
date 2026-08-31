#!/usr/bin/env python3
"""Generate or verify the immutable Elettra VECTO HVAC template release."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from elettra_core.vecto_templates import (  # noqa: E402
    canonical_template_release_bytes,
    template_release_sha256,
)

DEFAULT_OUTPUT = (
    REPOSITORY_ROOT
    / "elettra_core"
    / "data"
    / "vecto_hvac_5_1_3_r744_templates_v1.json"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate or verify the canonical VECTO HVAC templates"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"artifact path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify byte-for-byte equality without writing",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = args.output.resolve()
    expected = canonical_template_release_bytes()
    if args.check:
        try:
            actual = output.read_bytes()
        except FileNotFoundError:
            print(f"missing VECTO template release: {output}", file=sys.stderr)
            return 1
        if actual != expected:
            print(f"stale VECTO template release: {output}", file=sys.stderr)
            return 1
        print(f"ok {output} sha256={template_release_sha256()}")
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(expected)
    print(f"wrote {output} sha256={template_release_sha256()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
