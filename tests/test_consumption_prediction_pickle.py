from __future__ import annotations

import io
import json
import sys

from elettra_core import FEATURE_CONTRACT_VERSION, categorical_feature_contract
from simulation import consumption_prediction


def _metadata() -> dict:
    return {
        "model_type": "CombinedGreyboxQRF",
        "feature_contract_version": FEATURE_CONTRACT_VERSION,
        "categorical_feature_contract": categorical_feature_contract(),
        "selected_features": [],
    }


def _clear_legacy_symbols(monkeypatch) -> None:
    main_module = sys.modules["__main__"]
    for name in (
        "CombinedGreyboxQRF",
        "MechanicalGreyBox",
        "GreyBoxParams",
        "compute_aux_energy",
    ):
        monkeypatch.setattr(main_module, name, None, raising=False)


def _assert_legacy_symbols() -> None:
    main_module = sys.modules["__main__"]
    assert main_module.CombinedGreyboxQRF is consumption_prediction.CombinedGreyboxQRF
    assert main_module.MechanicalGreyBox is consumption_prediction.MechanicalGreyBox
    assert main_module.GreyBoxParams is consumption_prediction.GreyBoxParams
    assert main_module.compute_aux_energy is consumption_prediction.compute_aux_energy


def test_local_compatibility_loader_registers_trainer_pickle_symbols(
    tmp_path, monkeypatch
):
    _clear_legacy_symbols(monkeypatch)
    model_path = tmp_path / "model.joblib"
    model_path.write_bytes(b"trusted-model-placeholder")
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(json.dumps(_metadata()), encoding="utf-8")
    sentinel = object()

    def fake_load(source):
        assert source == str(model_path)
        _assert_legacy_symbols()
        return sentinel

    monkeypatch.setattr(consumption_prediction.joblib, "load", fake_load)
    predictor = consumption_prediction.ConsumptionPredictor()

    predictor.load_model_from_file(str(model_path), str(metadata_path))

    assert predictor.model is sentinel


def test_minio_loader_uses_the_same_pickle_symbol_registration(monkeypatch):
    _clear_legacy_symbols(monkeypatch)
    metadata = _metadata()
    sentinel = object()

    monkeypatch.setattr(
        consumption_prediction,
        "download_model_from_minio",
        lambda **_kwargs: (b"trusted-model-placeholder", metadata),
    )

    def fake_load(source):
        assert isinstance(source, io.BytesIO)
        _assert_legacy_symbols()
        return sentinel

    monkeypatch.setattr(consumption_prediction.joblib, "load", fake_load)
    predictor = consumption_prediction.ConsumptionPredictor()

    predictor.load_model("release-id")

    assert predictor.model is sentinel
