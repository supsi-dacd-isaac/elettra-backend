# VECTO HVAC SSM provenance and runtime boundary

The Python module implements only VECTO 5.1.3's bus HVAC steady-state model.
It is not a replacement for a complete VECTO vehicle, mission, driving-cycle
or powertrain simulation.

The authoritative source used for the transcription is the
[official VECTO 5.1.3 archive](https://code.europa.eu/vecto/vecto/-/archive/Release/v5.1.3/vecto-Release-v5.1.3.tar.gz),
at commit `cef1f3d260afa7f7c6ec09981d821e545d21b249`.

The upstream implementation is copyright 2012-2022 European Commission,
DG_CLIMA and licensed under EUPL-1.2. SUPSI-DACD-ISAAC created the Python
behavioural transcription on 2026-08-31. The prominent notice at the top of
`elettra_core/vecto_ssm.py` records the translation, validation and API changes.

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

## Shared runtime boundary

The faithful implementation lives in `elettra_core.vecto_ssm` so training and
backend inference can pin one installable implementation. The historical
`app.services.vecto_ssm` path is a compatibility re-export only.

Explicit Elettra scenario declarations and their environmental adapter live in
`elettra_core.vecto_templates`. They remain separate from the faithful SSM:
geometry, passengers, non-HVAC baseline and diesel-heater availability are
caller-owned inputs, not values inferred by VECTO. See
`VECTO_HVAC_TEMPLATES.md` for their derivation, contracts and release process.

Making a VECTO-based stack available does not implicitly select it. The active
model manifest must pin the compatible auxiliary contract and template hash;
the legacy auxiliary curves remain a separate stack.

## Distribution boundary

The official DLLs and the archive are oracle inputs only and are never copied
into the production wheel or image. The local oracle image is non-distributable
and must not be pushed to a registry. Any release containing the Python
transcription must include the MIT and EUPL-1.2 texts, the third-party notice,
and a link to the exact public `elettra-core` source tag. Publication remains
blocked until the release owner approves the repository's MIT/EUPL component
boundary and corresponding source-availability procedure.
