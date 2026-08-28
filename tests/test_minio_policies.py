import json
from pathlib import Path


POLICY_DIR = Path(__file__).resolve().parents[1] / "deploy" / "minio"
PROFILE_BUCKET = "arn:aws:s3:::elevation-profiles"
GTFS_PROFILE_BUCKET = "arn:aws:s3:::elevation-profiles-gtfs"
MODEL_BUCKET = "arn:aws:s3:::consumption-models"
OBJECT_WRITE_ACTIONS = {
    "s3:PutObject",
    "s3:DeleteObject",
    "s3:AbortMultipartUpload",
}


def _load(name: str) -> dict:
    with (POLICY_DIR / name).open(encoding="utf-8") as handle:
        return json.load(handle)


def _statements(policy: dict, effect: str) -> list[dict]:
    return [statement for statement in policy["Statement"] if statement["Effect"] == effect]


def _actions(statements: list[dict]) -> set[str]:
    return {
        action
        for statement in statements
        for action in statement.get("Action", [])
    }


def _resources(statements: list[dict]) -> set[str]:
    return {
        resource
        for statement in statements
        for resource in statement.get("Resource", [])
    }


def test_policy_documents_are_explicit_and_well_formed():
    for path in sorted(POLICY_DIR.glob("*.json")):
        policy = _load(path.name)
        assert policy["Version"] == "2012-10-17"
        assert policy["Statement"]
        assert "s3:*" not in _actions(policy["Statement"])


def test_backend_is_read_only_and_cannot_read_worker_namespaces():
    policy = _load("elevation-backend-readonly.json")
    allowed = _statements(policy, "Allow")
    denied = _statements(policy, "Deny")

    assert _actions(allowed) == {
        "s3:GetBucketLocation",
        "s3:ListBucket",
        "s3:GetObject",
    }
    assert OBJECT_WRITE_ACTIONS <= _actions(denied)

    denied_resources = {
        resource
        for statement in denied
        for resource in statement.get("Resource", [])
        if "s3:GetObject" in statement.get("Action", [])
    }
    assert denied_resources == {
        f"{PROFILE_BUCKET}/backups/*",
        f"{PROFILE_BUCKET}/._staging/*",
        f"{PROFILE_BUCKET}/._health/*",
    }
    assert {PROFILE_BUCKET, GTFS_PROFILE_BUCKET, MODEL_BUCKET} <= {
        resource for statement in allowed for resource in statement.get("Resource", [])
    }


def test_worker_can_manage_aux_objects_but_cannot_touch_releases():
    policy = _load("elevation-worker.json")
    allowed = _statements(policy, "Allow")
    denied = _statements(policy, "Deny")

    assert {
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:AbortMultipartUpload",
    } <= _actions(allowed)
    release_denies = [
        statement
        for statement in denied
        if f"{PROFILE_BUCKET}/releases/*" in statement.get("Resource", [])
    ]
    assert release_denies
    assert {
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:AbortMultipartUpload",
    } <= _actions(release_denies)
    assert all(
        not resource.startswith(GTFS_PROFILE_BUCKET)
        for resource in _resources(policy["Statement"])
    )


def test_publisher_is_release_scoped_manifest_last_capable_and_non_deleting():
    policy = _load("elevation-release-publisher.json")
    allowed = _statements(policy, "Allow")
    denied = _statements(policy, "Deny")
    release_resource = f"{GTFS_PROFILE_BUCKET}/releases/*"

    object_allows = [
        statement
        for statement in allowed
        if release_resource in statement.get("Resource", [])
    ]
    assert object_allows
    assert {
        "s3:GetObject",
        "s3:PutObject",
        "s3:AbortMultipartUpload",
    } <= _actions(object_allows)
    assert "s3:DeleteObject" not in _actions(allowed)
    assert any(
        "s3:DeleteObject" in statement.get("Action", [])
        and release_resource in statement.get("Resource", [])
        for statement in denied
    )
    assert all(
        resource.startswith(GTFS_PROFILE_BUCKET)
        for resource in _resources(policy["Statement"])
    )
