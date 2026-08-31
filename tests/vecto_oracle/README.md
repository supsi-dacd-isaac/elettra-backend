# VECTO 5.1.3 SSM oracle

This harness calls the official `SSMTOOL` implementation from the .NET 8
`VectoCore.dll` distributed with VECTO 5.1.3. It is intentionally independent
of the Python implementation so a shared transcription error cannot make both
sides of a regression test pass.

The VECTO binaries are not vendored. The safe verification command checks the
three binary hashes, checks the CLI/Core versions, builds the isolated oracle,
runs all cases, and compares the result without writing files:

```sh
python tests/vecto_oracle/verify.py --vecto-bin /path/to/tools/vecto-bin/net80
```

Only an intentional update uses write mode:

```sh
python tests/vecto_oracle/verify.py \
  --vecto-bin /path/to/tools/vecto-bin/net80 \
  --update-golden
```

Why this does not run a complete `vectocmd` job: the CLI adds a declaration,
mission, powertrain and cycle around the SSM. Calling the official `SSMTOOL`
library exercises exactly the subsystem transcribed by
`elettra_core.vecto_ssm`, while keeping every input controlled. The historical
`app.services.vecto_ssm` module is only a compatibility re-export. The release
and CLI identity are additionally checked with `vectocmd.dll -h`.

The checked-in matrix exercises every reachable official heating-distribution
case HD1 through HD12, electrical and mechanical heat pumps, electrical and
fuel heaters, the 17 °C cooling cutoff, heating/cooling capacity limits,
ventilation, engine waste heat and technology-benefit clamping.

## Scope boundary

Passing these golden tests means that the Python HVAC SSM matches the official
`SSMTOOL` for the same explicit declaration inputs. It does not make this
module a replacement for a complete VECTO simulation. In particular, it does
not derive bus declaration parameters, simulate a driving cycle or powertrain,
or calculate electrical, pneumatic and steering auxiliaries outside the HVAC
SSM. Such loads must come from their official VECTO modules or be passed as an
explicit caller-owned baseline.
