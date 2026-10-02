# Contributing

## Changes that alter behavior

Update the normative contract and affected examples in the same pull request
as the implementation. Record the rationale in an ADR when changing ownership,
start/abort semantics, measurement schema, transport, or runtime ABI. Add an
acceptance test for behavior that crosses a hardware or Bluesky boundary.
Do not change hardware evidence status based on simulation alone.

## Versions and reproducibility

Use the development status and capability register to distinguish a template,
simulator, and commissioned hardware backend. Package version tags do not certify
hardware acceptance. Record Python/package versions, firmware, runtime ABI and
SHA-256, program hash, and contract revision in run configuration. Never silently
overwrite an incompatible runtime.

Use `uv sync --locked` and `uv run --locked` for development. Commit `uv.lock`;
change dependencies with `uv add`, `uv add --group GROUP`, or edit pyproject and
run `uv lock`. Prefer targeted updates (`uv lock --upgrade-package NAME`) over
unrelated dependency churn. The repository lock is a development/CI baseline;
beamline deployments still require validation for their OS, hardware, and backend.

Follow Conventional Commits, shared with McSAS3/MoDaCor: `feat`/`enh` add a minor
version, `fix`/`perf` a patch, and `!` or `BREAKING CHANGE:` marks incompatibility.
While major version is zero, breaking changes increment the minor version.
`ci`, `docs`, `test`, and other housekeeping types do not normally bump a version.
Do not stamp release numbers manually; merge the semantic-release preparation PR.

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

## Releases

Reusable CI jobs test, build and check docs before tagging a prepared release.
Release preparation changes version, changelog and lockfile through normal PR
review. `uvx --from python-semantic-release==10.7.0 semantic-release version --print`
is a read-only preview. GitHub Releases receive the exact tested distributions.
PyPI Trusted Publishing is configured but runs only after the repository variable
`PUBLISH_PYPI` is explicitly enabled and its environment/publisher is configured.
Resolve the pending license before enabling public package distribution. Document
uncommissioned capabilities rather than claiming hardware acceptance from CI.
See [development and releases](docs/development.md) for setup and recovery details.
