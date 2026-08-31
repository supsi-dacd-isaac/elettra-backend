# Third-party notices

## VECTO 5.1.3 HVAC steady-state model

`elettra_core/vecto_ssm.py` is a behavioural transcription of the bus HVAC
steady-state model distributed with VECTO 5.1.3. The source implementation is
copyright 2012-2022 European Commission, DG_CLIMA and licensed under
EUPL-1.2. SUPSI-DACD-ISAAC created the Python transcription on 2026-08-31;
the prominent notice in that file states the nature of the modifications. The
transcribed file is marked separately with its SPDX identifier and is not
covered by the repository's default MIT declaration.

The upstream project metadata also identifies Graz University of Technology
and the European Commission's DG JRC among the VECTO authors.

`app/services/vecto_ssm.py` is a compatibility re-export and contains no copy
of the transcribed implementation. The Elettra-authored scenario templates in
`elettra_core/vecto_templates.py` are configuration around the SSM and must not
be described as official VECTO declarations.

The official archive and its .NET assemblies are deliberately not vendored or
copied into the application image. They are supplied externally, in read-only
form, only when reproducing the oracle tests.

The complete upstream EUPL-1.2 text is distributed at
`LICENSES/EUPL-1.2.txt` (SHA-256
`6fc9e709ccbfe0d77fbffa2427a983282be2eb88e47b1cdb49f21a83b4d1e665`),
copied byte-for-byte from the VECTO 5.1.3 source archive. The wheel also
contains it as `elettra_core/licenses/EUPL-1.2.txt` together with this notice.

The SUPSI release owner approved this component boundary for public
distribution on 2026-08-31. The combined `elettra-core` package declares the
SPDX expression `MIT AND EUPL-1.2`: MIT covers the Elettra-authored files and
EUPL-1.2 covers the separately marked VECTO transcription. This project
decision does not remove either licence's notices, source-availability or
redistribution obligations.

A release containing the transcription must identify the exact public source
tag that corresponds to the executable distribution. This also applies when
the software's essential functionality is offered over a network.

The local `elettra-vecto-oracle:5.1.3` test image contains official VECTO
assemblies and is an ephemeral, non-distributable verification artifact. It
must never be pushed to an image registry or reused as the production image.

Source: VECTO release 5.1.3, commit
`cef1f3d260afa7f7c6ec09981d821e545d21b249`, including `SSMRun.cs`,
`SSMCalculate.cs`, `SSMInputs.cs`, `DefaultClimatic.aenv` and
`HeatingDistribution*.csv`.

Official source archive:
<https://code.europa.eu/vecto/vecto/-/archive/Release/v5.1.3/vecto-Release-v5.1.3.tar.gz>.

Corresponding Elettra source release:
<https://github.com/supsi-dacd-isaac/elettra-backend/tree/elettra-core-v2.1.0>.
