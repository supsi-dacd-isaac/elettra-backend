#!/usr/bin/env bash
# Classify publication channels without consulting mutable branch state.
set -euo pipefail
ref="${1:?Git ref is required}"
if [[ "$ref" =~ ^refs/tags/v[0-9]+\.[0-9]+\.[0-9]+-rc\.[1-9][0-9]*$ ]]; then
  echo prerelease
elif [[ "$ref" =~ ^refs/tags/v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo stable
elif [[ "$ref" == refs/heads/* ]]; then
  echo candidate
else
  echo "Unsupported release ref: $ref" >&2
  exit 1
fi
