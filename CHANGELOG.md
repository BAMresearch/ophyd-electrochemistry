# Changelog

<!-- version list -->

## Unreleased

- Expand implementation contract to revision 0.2: first-class PRBS and generic
  finite arbitrary-waveform intent; multisine lowers through the shared mechanism.
- Specify deterministic generation, separate source/measurement timing, waveform
  provenance and M1/M2 acceptance; add G08 for hardware waveform fidelity/phase.
- No waveform Python models, generators, runtime or driver implementation added.
- Adopt uv/uvx setup, a committed cross-platform lockfile and dependency groups.
- Use a single dynamic version source and Python Semantic Release commit rules.
- Add reusable CI jobs and release preparation PRs based on MoDaCor/McSAS3.
- Build/tag the tested commit; configure optional PyPI Trusted Publishing.
- Normalize the development version to the unreleased `0.0.0` SemVer sentinel.

## 0.0.0.dev0 — 2026-10-02

- Document implementation contract revision 0.1.
- Define typed protocol/configuration/interface templates and module boundaries.
- Record source-supported assumptions and unresolved hardware validation gates.
- Add GitHub Actions, documentation build, package build, and RunEngine witness tests.
- Do not implement transport, runtime, simulator, or operational device behavior.
