from types import SimpleNamespace

import pytest

from app.core.config import validate_runtime_secrets


def _settings(secret: str, *, debug: bool = False):
    return SimpleNamespace(secret_key=secret, debug=debug)


@pytest.mark.parametrize(
    "secret",
    [
        "CHANGE_ME__runtime_environment_must_override",
        "changeme",
        "secret",
        "too-short",
        "   ",
    ],
)
def test_placeholder_or_short_secret_is_rejected_in_runtime(secret):
    with pytest.raises(RuntimeError, match="APP_SECRET_KEY"):
        validate_runtime_secrets(_settings(secret), testing=False)


def test_strong_runtime_secret_is_accepted():
    validate_runtime_secrets(
        _settings("runtime-secret-with-more-than-32-characters"),
        testing=False,
    )


@pytest.mark.parametrize("debug,testing", [(True, False), (False, True)])
def test_placeholder_is_allowed_only_for_debug_or_tests(debug, testing):
    validate_runtime_secrets(
        _settings("CHANGE_ME__runtime_environment_must_override", debug=debug),
        testing=testing,
    )
