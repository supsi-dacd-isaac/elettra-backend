"""Publish one multi-platform index from the native jobs' verified digests."""

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


def checked_digest(value):
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise ValueError("Invalid image digest")
    return value


def read_sources(directory, architectures, image):
    if (
        not architectures
        or len(set(architectures)) != len(architectures)
        or not set(architectures) <= {"amd64", "arm64"}
    ):
        raise ValueError("Invalid requested architectures")
    expected = {f"{arch}.digest" for arch in architectures}
    if {path.name for path in directory.iterdir()} != expected:
        raise ValueError("Missing or unexpected platform digest artifacts")
    return [
        f"{image}@{checked_digest((directory / f'{arch}.digest').read_text().strip())}"
        for arch in architectures
    ]


def validate_index(index, architectures):
    """Reject missing/duplicate platforms or lost build attestations."""
    platforms = {}
    attested = set()
    digests = set()
    manifests = index.get("manifests", [])
    for item in manifests:
        digest = checked_digest(item["digest"])
        if digest in digests:
            raise ValueError("Duplicate manifest digest")
        digests.add(digest)
        platform = item.get("platform", {})
        arch = platform.get("architecture")
        if platform.get("os") == "linux" and arch in architectures:
            if arch in platforms:
                raise ValueError("Duplicate runtime architecture")
            platforms[arch] = digest
        elif platform == {"architecture": "unknown", "os": "unknown"}:
            annotations = item.get("annotations", {})
            if annotations.get("vnd.docker.reference.type") != "attestation-manifest":
                raise ValueError("Unexpected non-runtime manifest")
            attested.add(checked_digest(annotations.get("vnd.docker.reference.digest")))
        else:
            raise ValueError("Unexpected runtime platform")
    if set(platforms) != set(architectures):
        raise ValueError("Missing runtime architecture")
    if attested != set(platforms.values()):
        raise ValueError("Missing or unrelated platform attestations")
    return sorted(digests)


def docker_json(*args):
    return json.loads(subprocess.check_output(["docker", "buildx", "imagetools", *args], text=True))


def publish(image, architectures, directory, metadata):
    sources = read_sources(directory, architectures, image)
    tags = metadata.get("tags", [])
    if not isinstance(tags, list) or not tags or not all(
        isinstance(tag, str)
        and re.fullmatch(re.escape(image) + r":[\w][\w.-]{0,127}", tag)
        for tag in tags
    ):
        raise ValueError("Invalid or missing publication tags")

    # Validate the index before any version/latest tag can be assigned. Buildx
    # combines the platform indexes, including their SBOM/provenance manifests.
    proposed = docker_json("create", "--dry-run", *sources)
    expected_digests = validate_index(proposed, architectures)
    with tempfile.TemporaryDirectory() as temp:
        result = Path(temp) / "metadata.json"
        command = ["docker", "buildx", "imagetools", "create", "--metadata-file", str(result)]
        for tag in tags:
            command.extend(["--tag", tag])
        subprocess.run([*command, *sources], check=True)
        digest = checked_digest(json.loads(result.read_text())["containerimage.descriptor"]["digest"])

    # Read by immutable digest, not a mutable alias that another run may update.
    published = docker_json("inspect", "--raw", f"{image}@{digest}")
    if validate_index(published, architectures) != expected_digests:
        raise ValueError("Published index differs from the verified platform manifests")
    return digest


def main():
    digest = publish(
        os.environ["IMAGE_NAME"],
        json.loads(os.environ["IMAGE_ARCHITECTURES"]),
        Path(os.environ["RUNNER_TEMP"]) / "image-digests",
        json.loads(os.environ["IMAGE_METADATA"]),
    )
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"digest={digest}\n")
    print(f"Verified multi-platform image: {os.environ['IMAGE_NAME']}@{digest}")


if __name__ == "__main__":
    main()
