"""Offline regression checks for native image assembly and publication gates."""

import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("merge_image", ROOT / ".github/scripts/merge-image.py")
merge_image = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(merge_image)
IMAGE = "ghcr.io/supsi-dacd-isaac/elettra-backend"


def digest(number):
    return f"sha256:{number:064x}"


def image_index(architectures):
    manifests = []
    for number, arch in enumerate(architectures, 1):
        manifests.extend([
            {"digest": digest(number), "platform": {"architecture": arch, "os": "linux"}},
            {"digest": digest(number + 10), "platform": {"architecture": "unknown", "os": "unknown"},
             "annotations": {"vnd.docker.reference.type": "attestation-manifest",
                             "vnd.docker.reference.digest": digest(number)}},
        ])
    return {"schemaVersion": 2, "manifests": manifests}


class ImageAssemblyTests(unittest.TestCase):
    def test_single_and_multi_platform_indexes_retain_attestations(self):
        for architectures in [["amd64"], ["amd64", "arm64"]]:
            with self.subTest(architectures=architectures):
                self.assertEqual(len(merge_image.validate_index(image_index(architectures), architectures)),
                                 2 * len(architectures))

    def test_incomplete_or_ambiguous_indexes_are_rejected(self):
        original = image_index(["amd64", "arm64"])
        missing_arch = image_index(["amd64"])
        missing_attestation = copy.deepcopy(original)
        missing_attestation["manifests"].pop()
        wrong_os = copy.deepcopy(original)
        wrong_os["manifests"][0]["platform"]["os"] = "windows"
        duplicate_arch = copy.deepcopy(original)
        duplicate_arch["manifests"][2]["platform"]["architecture"] = "amd64"
        duplicate_digest = copy.deepcopy(original)
        duplicate_digest["manifests"][2]["digest"] = digest(1)
        unrelated_attestation = copy.deepcopy(original)
        unrelated_attestation["manifests"][1]["annotations"]["vnd.docker.reference.digest"] = digest(99)
        for index in [missing_arch, missing_attestation, wrong_os, duplicate_arch,
                      duplicate_digest, unrelated_attestation, {}]:
            with self.subTest(index=index), self.assertRaises(ValueError):
                merge_image.validate_index(index, ["amd64", "arm64"])

    def write_sources(self, directory, architectures):
        for number, arch in enumerate(architectures, 20):
            (directory / f"{arch}.digest").write_text(digest(number) + "\n")

    def test_missing_extra_and_invalid_digest_artifacts_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            self.write_sources(directory, ["amd64"])
            with self.assertRaises(ValueError):
                merge_image.read_sources(directory, ["amd64", "arm64"], IMAGE)
            with self.assertRaises(ValueError):
                merge_image.read_sources(directory, ["arm64"], IMAGE)
            (directory / "amd64.digest").write_text("latest")
            with self.assertRaises(ValueError):
                merge_image.read_sources(directory, ["amd64"], IMAGE)

    def test_missing_platform_stops_before_any_tag_is_published(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            self.write_sources(directory, ["amd64", "arm64"])
            with patch.object(merge_image, "docker_json", return_value=image_index(["amd64"])), \
                    patch.object(merge_image.subprocess, "run") as push:
                with self.assertRaises(ValueError):
                    merge_image.publish(IMAGE, ["amd64", "arm64"], directory,
                                        {"tags": [f"{IMAGE}:1.4.1"]})
                push.assert_not_called()

    def test_publication_uses_all_verified_digests_and_returns_the_index_digest(self):
        for architectures in [["amd64"], ["amd64", "arm64"]]:
            with self.subTest(architectures=architectures), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                self.write_sources(directory, architectures)
                index = image_index(architectures)
                tag = f"{IMAGE}:1.4.1-rc.2"

                def push(command, **kwargs):
                    self.assertTrue(kwargs["check"])
                    self.assertEqual(command.count("--tag"), 1)
                    self.assertIn(tag, command)
                    for number in range(20, 20 + len(architectures)):
                        self.assertIn(f"{IMAGE}@{digest(number)}", command)
                    Path(command[command.index("--metadata-file") + 1]).write_text(
                        json.dumps({"containerimage.descriptor": {"digest": digest(100)}})
                    )

                with patch.object(merge_image, "docker_json", side_effect=[index, index]) as inspect, \
                        patch.object(merge_image.subprocess, "run", side_effect=push):
                    result = merge_image.publish(IMAGE, architectures, directory, {"tags": [tag]})
                    self.assertEqual(result, digest(100))
                    inspect.assert_called_with("inspect", "--raw", f"{IMAGE}@{digest(100)}")

    def test_wrong_registry_or_missing_tags_never_reach_docker(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            self.write_sources(directory, ["amd64"])
            for tags in [[], ["other/image:latest"], [f"{IMAGE}:latest --append"]]:
                with self.subTest(tags=tags), patch.object(merge_image, "docker_json") as docker:
                    with self.assertRaises(ValueError):
                        merge_image.publish(IMAGE, ["amd64"], directory, {"tags": tags})
                    docker.assert_not_called()


class ReleaseWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.jobs = yaml.safe_load((ROOT / ".github/workflows/container-release.yml").read_text())["jobs"]

    def test_release_uses_two_native_runners_and_candidate_keeps_amd64(self):
        selector = next(step["run"] for step in self.jobs["prepare-image"]["steps"]
                        if step.get("id") == "target")
        for release, expected in [("true", {"amd64": "ubuntu-24.04", "arm64": "ubuntu-24.04-arm"}),
                                  ("false", {"amd64": "ubuntu-24.04"})]:
            with self.subTest(release=release), tempfile.TemporaryDirectory() as temp:
                output = Path(temp) / "outputs"
                subprocess.run(["bash", "-e", "-c", selector], check=True,
                               env={**os.environ, "IS_RELEASE": release, "GITHUB_OUTPUT": str(output),
                                    "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]})
                values = dict(line.split("=", 1) for line in output.read_text().splitlines())
                matrix = json.loads(values["matrix"])["include"]
                self.assertEqual({item["arch"]: item["runner"] for item in matrix}, expected)
                self.assertEqual(set(json.loads(values["architectures"])), set(expected))

    def test_publication_requires_tests_and_every_native_image_smoke_test(self):
        self.assertEqual(self.jobs["prepare-image"]["needs"], "verify")
        build = self.jobs["build-image"]
        self.assertEqual(build["needs"], "prepare-image")
        self.assertFalse(build.get("continue-on-error", False))
        self.assertIn("build-image", self.jobs["publish-image"]["needs"])
        steps = build["steps"]
        names = [step["name"] for step in steps]
        self.assertLess(names.index("Verify native image identity"), names.index("Upload verified image digest"))
        self.assertTrue(all(not step.get("continue-on-error", False) for step in steps))
        for step in steps:
            self.assertNotIn("setup-qemu-action", step.get("uses", ""))
        builder = next(step["with"] for step in steps if step.get("id") == "build")
        self.assertNotIn("tags", builder)
        self.assertIn("push-by-digest=true", builder["outputs"])
        self.assertTrue(builder["sbom"])
        self.assertEqual(builder["provenance"], "mode=max")
        self.assertIn("buildcache-${{ matrix.arch }}", builder["cache-from"])
        self.assertIn("buildcache-${{ matrix.arch }}", builder["cache-to"])
        self.assertIn("publish-image", self.jobs["release"]["needs"])


if __name__ == "__main__":
    unittest.main()
