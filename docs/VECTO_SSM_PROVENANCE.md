# VECTO HVAC SSM provenance and runtime boundary

The Python module implements only VECTO 5.1.3's bus HVAC steady-state model.
It is not a replacement for a complete VECTO vehicle, mission, driving-cycle
or powertrain simulation.

The Python API accepts an explicit environmental condition per invocation.
Callers can therefore evaluate a time series with changing temperature, solar
irradiance, COP and heater efficiency. The low-level official SSM DLL accepts
the same explicit inputs and is used as a test oracle. The backend does not
load the DLL at runtime.

The checked-in oracle contains 18 cases and 126 scalar results. Against the
official VECTO 5.1.3 .NET 8 assemblies, the largest observed absolute Python
difference is `1.14e-13 W`. The harness also verifies the release identity and
SHA-256 of `VectoCore.dll`, `VectoCommon.dll` and `vectocmd.dll` before use.

This equivalence statement is limited to the controlled HVAC SSM inputs and
outputs exercised by the oracle. It does not cover the complete `vectocmd`
workflow or auxiliaries outside HVAC. See `tests/vecto_oracle/README.md` for
the reproducible command and `THIRD_PARTY_NOTICES.md` for licensing.

## Production decision

VECTO is not wired into prediction or training in this release. Production
continues to use the existing auxiliary-consumption curves in
`buses_models.specs`; the training release continues to use the frozen
`hvac_exploration_results.json` input. Integrating the VECTO calculation is a
separate model change and requires a new validation and rollout.
