# Contributing

## Changes that alter behavior

Update the normative contract and affected examples in the same pull request
as the implementation. Record the rationale in an ADR when changing ownership,
start/abort semantics, measurement schema, transport, or runtime ABI. Add an
acceptance test for behavior that crosses a hardware or Bluesky boundary.
Do not change hardware evidence status based on simulation alone.

## Versions and reproducibility

Use development versions until hardware acceptance is complete. Tag releases;
record Python/package versions, firmware, runtime ABI and SHA-256, program hash,
and contract revision in run configuration. Never silently overwrite an
incompatible instrument runtime. A deployment lockfile with exact dependency
versions must be generated and tested for each beamline environment before use.

## Pull request checks

Run the commands in README. CI builds docs and the Python distribution and
checks lint, formatting, typing, and contract witness tests. Future runtime
tests must verify safety, timing, and data behavior rather than just repeat
the implementation. Hardware tests are excluded by default and cannot pass by
being skipped. No real DUT is used for initial commissioning.

## Transport and TSP changes

Keep TSP in package resources. Review the generated TriggerFlow model and its
block/resource budget. Serialize complete query/response transactions. Never
retry an ambiguous START, clear, or output-on command automatically. Require
bounded transport operations so abort cannot be starved by buffer retrieval.

## Release gate

No public package release is configured. First resolve licensing, reproduce CI,
complete the target firmware's bench evidence, and document unsupported features.
