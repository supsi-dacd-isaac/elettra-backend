"""Compatibility imports for the shared VECTO HVAC implementation.

New code must import these primitives from :mod:`elettra_core.vecto_ssm`.
This module remains so existing backend imports and historical pickle/test
references continue to resolve while training and inference share one source.
"""

from elettra_core.vecto_ssm import (
    ElectricHeaterType,
    FloorType,
    HeatPumpType,
    VectoAuxResult,
    VectoComfortPolicy,
    VectoEnvironmentalCondition,
    VectoSsmInputs,
    VECTO_DEFAULT_COMFORT_POLICY,
    _heating_distribution_case,
    _ssm_calculate,
    default_environmental_condition,
    vecto_auxiliary_power,
)

__all__ = [
    "ElectricHeaterType",
    "FloorType",
    "HeatPumpType",
    "VectoAuxResult",
    "VectoComfortPolicy",
    "VectoEnvironmentalCondition",
    "VectoSsmInputs",
    "VECTO_DEFAULT_COMFORT_POLICY",
    "default_environmental_condition",
    "vecto_auxiliary_power",
]
