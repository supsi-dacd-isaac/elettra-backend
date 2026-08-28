from pathlib import Path


REQUIREMENTS = Path(__file__).resolve().parents[1] / "requirements.txt"


def test_pandas_runtime_version_is_bounded_for_feature_contract_v2():
    requirements = {
        line.strip()
        for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert "pandas>=2.1.4,<3" in requirements
    assert sum(requirement.lower().startswith("pandas") for requirement in requirements) == 1
