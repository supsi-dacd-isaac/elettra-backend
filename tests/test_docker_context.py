from pathlib import Path

from elettra_core.vecto_templates import VECTO_TEMPLATE_RELEASE


def test_docker_context_includes_active_vecto_template() -> None:
    """Keep the runtime data asset in the API image allow-list."""

    repository = Path(__file__).resolve().parents[1]
    rules = {
        line.strip()
        for line in (repository / ".dockerignore").read_text(encoding="utf-8").splitlines()
    }
    filename = VECTO_TEMPLATE_RELEASE.replace("-", "_").replace(".", "_")
    relative_template = Path("elettra_core") / "data" / f"{filename}.json"

    assert f"!{relative_template.as_posix()}" in rules
