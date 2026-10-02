# Development, uv and semantic releases

## Reproducible environments

Install uv using [Astral's installation instructions](https://docs.astral.sh/uv/getting-started/installation/).
Run `uv python install` and `uv sync --locked` in the repository. The development
Python pin is 3.12; the compatibility target remains 3.11–3.13. Select `.venv`
in VS Code. `uv run --locked` runs commands in that environment.

`pyproject.toml` defines runtime dependencies and optional `visa` support.
Dependency groups separate `test`, `lint`, `docs`, `build`, and `release` tools;
default `dev` includes the first four. `uv.lock` records all groups/extras and
platform/Python resolutions. Isolated builds use constrained setuptools/wheel
versions in `tool.uv.build-constraint-dependencies`.

| Task | Command |
|---|---|
| Default development install | `uv sync --locked` |
| PyVISA backend development | `uv sync --locked --extra visa` |
| Tests | `uv run --locked pytest` |
| Lint/type check | `uv run --locked ruff check .` / `uv run --locked mypy src scripts` |
| Strict docs build | `uv run --locked mkdocs build --strict` |
| Distributions/metadata | `uv build` / `uv run --locked twine check --strict dist/*` |
| Release tools in project | `uv sync --locked --group release` |
| Read-only standalone release preview | `uvx --from python-semantic-release==10.7.0 semantic-release version --print` |

`uvx` tool isolation is suitable for release preview; tests require the installed
project and its dependencies. A shell-globbing Windows terminal may need explicit
wheel/sdist filenames for `twine check`; CI uses a shell that expands `dist/*`.

After changing dependencies run `uv lock`, review the diff, and commit both
pyproject and lockfile. `--locked` rejects stale metadata; it does not update the
lock. CI syncs only the necessary groups, then uses `--no-sync` for commands to
preserve that selection. Changes to the dynamic package version also require
`uv lock`; release preparation does this automatically.

The changelog's `<!-- version list -->` marker is required by the configured
semantic-release update template. Preserve it so new release sections are inserted.

## Reference implementation and adaptations

Reviewed on 2026-10-02:

- [McSAS3 pyproject](https://github.com/BAMresearch/McSAS3/blob/main/pyproject.toml)
  (blob `4b74d816453ecc360796e3173e02ff7c1fd368fc`): setuptools dynamic version,
  Python Semantic Release and `enh` as a feature commit type.
- [MoDaCor pyproject](https://github.com/BAMresearch/MoDaCor/blob/main/pyproject.toml)
  (blob `2ef16df0e01f0492fde30ba915289c48f9463f6e`): same version source and
  main-only releases.
- [MoDaCor CI-CD](https://github.com/BAMresearch/MoDaCor/blob/main/.github/workflows/ci-cd.yml),
  [release PR](https://github.com/BAMresearch/MoDaCor/blob/main/.github/workflows/release-pr.yml)
  and [release tagging](https://github.com/BAMresearch/MoDaCor/blob/main/.github/workflows/release.yml):
  reusable jobs, release preparation through branch protection, top-level OIDC
  PyPI publishing.

This adaptation uses uv instead of pip/tox, retains MkDocs and Ruff/mypy, and
avoids adding coverage or standalone-binary infrastructure before implementation
exists. The existing `ci.yml` filename is retained to make overlay updates safe.
Docs are built as downloadable workflow artifacts; a Pages site is not claimed.
All package build/tag/publish jobs use the tested event SHA, not a moving `main`.

## Release lifecycle

1. Normal commits/PRs run the reusable tests, build and docs jobs.
2. A successful main push creates/updates `release/semantic-release`, with version,
   changelog and lockfile changes. No version commit is pushed directly to main.
3. Merge that release PR through review and branch protection. Its main run must
   pass tests/docs/build before tagging the exact tested SHA and creating a GitHub
   Release with the same wheel/sdist artifacts.
4. PyPI publishing runs only for an eligible release and `PUBLISH_PYPI=true`.

The source's `0.0.0` is the initial SemVer sentinel. No `v0.0.0` tag is created.
The first release can be prepared without a previous tag; the existing `enh:`
framework commit provides feature history. `contract-v0.1` is a design revision,
not a package release tag. Only tags matching `v{version}` enter package history.

Rerunning CI on the exact release commit can finish a partially failed tag,
GitHub Release or PyPI upload. A later commit carrying the already-released version
does not publish again. GitHub assets are replaced on that exact-commit rerun;
PyPI skips distributions already uploaded and retains its immutable files.

Examples: `enh: add simulated acquisition` -> minor; `fix: preserve abort data`
-> patch; `feat!: change collection schema` -> breaking/minor while below 1.0.
`ci: adopt uv and release workflows` alone does not cause a version bump.
The version stays in `__init__.py`; setuptools reads it without importing hardware
or requiring runtime dependencies in the isolated build environment. MkDocs
does not duplicate a hard-coded package version.

## One-time GitHub configuration

The overlay does not change account or repository settings. Enable Actions and
allow GitHub Actions to create pull requests. The release jobs request only the
necessary contents/PR write permissions; test/build/docs jobs use read access.

PRs created with `GITHUB_TOKEN` do not automatically trigger other workflows.
If required PR checks must start automatically, configure `RELEASE_PR_TOKEN`
using an appropriately scoped GitHub App token or fine-grained token with
repository Contents/Pull requests write access. Otherwise run the test workflow
on the release branch, or push a human-authored change to trigger PR checks;
follow the branch's configured protection requirements.

For PyPI, first replace the pending license, create the `pypi` environment, and
register a Trusted Publisher for owner `BAMresearch`, repository
`ophyd-electrochemistry`, workflow `ci.yml`, environment `pypi`. Then set repository
variable `PUBLISH_PYPI=true`. No long-lived PyPI credential is required. Until
then tests, docs, build, release preparation and GitHub versioning can run.

## Applying chat updates

Unpack the changed-files archive **in the repository root**. Paths begin with
`README.md`, `pyproject.toml`, `docs/`, and `.github/`; there is no enclosing
repository directory, `.git`, environment, or generated distribution inside it.
Review `git diff` and new files, run the documented checks, then commit/upload.
Use Git/VS Code to commit dot-prefixed workflow/configuration files as well.
The current GitHub baseline also tracks an old `examples/__pycache__` bytecode
file; `.gitignore` prevents new bytecode, but removing an already tracked cache
requires `git rm --cached -r examples/__pycache__` in your checkout.
