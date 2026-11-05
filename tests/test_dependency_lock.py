from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
IMMUTABLE_ARCHIVE = re.compile(
    r"https://github\.com/[^/]+/[^/]+/archive/[0-9a-f]{40}\.zip$"
)


def _requirements(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_direct_runtime_dependencies_are_exact_or_immutable():
    requirements = _requirements(ROOT / "requirements.txt")

    assert requirements
    for requirement in requirements:
        if " @ " in requirement:
            assert IMMUTABLE_ARCHIVE.search(requirement), requirement
        else:
            assert "==" in requirement, requirement
            assert all(operator not in requirement for operator in (">=", "<=", "~=", "!="))


def test_transitive_lock_contains_only_exact_or_immutable_requirements():
    requirements = _requirements(ROOT / "requirements.lock")

    assert requirements
    for requirement in requirements:
        if requirement.startswith("--") or requirement.startswith("#"):
            continue
        if " @ " in requirement:
            assert IMMUTABLE_ARCHIVE.search(requirement), requirement
        else:
            assert "==" in requirement, requirement


def test_build_and_release_workflow_install_the_lock():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/container-release.yml").read_text(
        encoding="utf-8"
    )

    assert "COPY requirements.lock" in dockerfile
    assert "-r requirements.lock" in dockerfile
    assert "-r requirements.lock" in workflow
    assert "-r requirements.txt" not in dockerfile
    assert "-r requirements.txt" not in workflow
