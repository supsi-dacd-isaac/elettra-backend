from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def test_rc_publication_does_not_move_stable_image_aliases():
    for ref, expected in [("refs/tags/v1.4.0-rc.1", "prerelease"), ("refs/tags/v1.4.0", "stable"), ("refs/heads/fix/annual-analysis", "candidate")]:
        result = subprocess.run(["bash", str(ROOT / ".github/scripts/release-channel.sh"), ref], text=True, capture_output=True, check=True)
        assert result.stdout.strip() == expected
    invalid = subprocess.run(["bash", str(ROOT / ".github/scripts/release-channel.sh"), "refs/tags/v1.4.0-invalid"], capture_output=True)
    assert invalid.returncode != 0
    workflow = (ROOT / ".github/workflows/container-release.yml").read_text()
    assert "flavor: latest=false" in workflow
    assert "type=raw,value=latest,enable=${{ steps.channel.outputs.name == 'stable' }}" in workflow
    assert "release_flags+=(--prerelease --latest=false)" in workflow
