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

Before distributing a build containing the transcription, the release owner
must complete the project's license review. This notice records provenance; it
does not assert that the MIT and EUPL-1.2 licensing obligations are compatible
for every distribution scenario.

That review must also decide the license expression for the combined
`elettra-core` distribution. Until then, the project metadata deliberately
lists both license texts and this notice without claiming a combined license
expression. A release containing the transcription must identify the exact
public source tag that corresponds to the executable distribution. This also
applies when the software's essential functionality is offered over a network.

The local `elettra-vecto-oracle:5.1.3` test image contains official VECTO
assemblies and is an ephemeral, non-distributable verification artifact. It
must never be pushed to an image registry or reused as the production image.

Source: VECTO release 5.1.3, commit
`cef1f3d260afa7f7c6ec09981d821e545d21b249`, including `SSMRun.cs`,
`SSMCalculate.cs`, `SSMInputs.cs`, `DefaultClimatic.aenv` and
`HeatingDistribution*.csv`.

Official source archive:
<https://code.europa.eu/vecto/vecto/-/archive/Release/v5.1.3/vecto-Release-v5.1.3.tar.gz>.

Corresponding Elettra source release (available only after the release owner
approves and publishes it):
<https://github.com/supsi-dacd-isaac/elettra-backend/tree/elettra-core-v2.1.0>.
