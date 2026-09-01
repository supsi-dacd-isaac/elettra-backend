"""Deterministic identity for the installed shared-core source tree."""

from __future__ import annotations

import hashlib
from pathlib import Path


# Solaris' physical payload convention.  This value is shared by training and
# inference and must be declared by every OGD-anchored model artifact.
PASSENGER_MASS_KG = 68.0


def source_tree_sha256() -> str:
    """Hash every shipped core Python/JSON source using stable relative paths."""

    root = Path(__file__).resolve().parent
    files = sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix in {".py", ".json"}
        ),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    if not files:
        raise RuntimeError("elettra-core source tree is empty")
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        payload = path.read_bytes()
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


__all__ = ["PASSENGER_MASS_KG", "source_tree_sha256"]
