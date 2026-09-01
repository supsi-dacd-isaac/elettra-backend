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


def test_clean_docker_context_contains_a_non_secret_runtime_config() -> None:
    """The API image must not depend on an ignored operator config file."""

    repository = Path(__file__).resolve().parents[1]
    image_config = repository / "config" / "elettra-config.image.yaml"
    dockerfile = (repository / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (repository / ".dockerignore").read_text(encoding="utf-8")

    assert image_config.is_file()
    assert "CHANGE_ME__runtime_environment_must_override" in image_config.read_text(
        encoding="utf-8"
    )
    assert "!config/elettra-config.image.yaml" in dockerignore
    assert (
        "cp /app/config/elettra-config.image.yaml "
        "/app/config/elettra-config.docker.yaml"
    ) in dockerfile
