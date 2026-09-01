import re

from elettra_core import PASSENGER_MASS_KG, source_tree_sha256


def test_core_source_tree_and_passenger_mass_contract() -> None:
    assert PASSENGER_MASS_KG == 68.0
    assert re.fullmatch(r"[0-9a-f]{64}", source_tree_sha256())
