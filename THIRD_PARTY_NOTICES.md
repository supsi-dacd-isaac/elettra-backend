# Third-party notices

## VECTO 5.1.3 HVAC steady-state model

`app/services/vecto_ssm.py` is a behavioural transcription of the bus HVAC
steady-state model distributed with VECTO 5.1.3. The source implementation is
copyright the European Union and licensed under EUPL-1.2. The transcribed file
is marked separately with its SPDX identifier and is not covered by the
repository's default MIT declaration.

The official archive and its .NET assemblies are deliberately not vendored or
copied into the application image. They are supplied externally, in read-only
form, only when reproducing the oracle tests.

Before distributing a build containing the transcription, the release owner
must complete the project's license review. This notice records provenance; it
does not assert that the MIT and EUPL-1.2 licensing obligations are compatible
for every distribution scenario.

Source: VECTO release 5.1.3, commit
`cef1f3d260afa7f7c6ec09981d821e545d21b249`, including `SSMRun.cs`,
`SSMCalculate.cs`, `SSMInputs.cs`, `DefaultClimatic.aenv` and
`HeatingDistribution*.csv`.
