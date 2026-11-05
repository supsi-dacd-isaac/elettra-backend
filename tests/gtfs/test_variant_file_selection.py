from pathlib import Path


def test_variant_file_selection_does_not_spawn_ls():
    source = (
        Path(__file__).resolve().parents[2] / "app/routers/gtfs.py"
    ).read_text(encoding="utf-8")

    assert "os.path.getsize" in source
    assert "subprocess.run(['ls'" not in source
    assert "import subprocess" not in source
