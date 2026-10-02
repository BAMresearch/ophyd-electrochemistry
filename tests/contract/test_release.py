"""Release gating tests use temporary Git repositories and never push or publish."""

import subprocess
from pathlib import Path

import pytest

from scripts.release import VERSION_FILE, read_version, release_candidate


def make_repo(tmp_path: Path, version: str, changelog: str) -> Path:
    source = tmp_path / VERSION_FILE
    source.parent.mkdir(parents=True)
    source.write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    for command in [
        ["git", "init", "-b", "main"],
        ["git", "config", "user.name", "Release Test"],
        ["git", "config", "user.email", "release-test@localhost"],
        ["git", "add", "."],
        ["git", "commit", "-m", "enh: initial framework"],
    ]:
        subprocess.run(command, cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def test_bootstrap_is_not_a_release(tmp_path):
    root = make_repo(tmp_path, "0.0.0", "# Changelog\n")
    assert read_version(root) == "0.0.0"
    assert release_candidate(root)["ready"] == "false"


def test_untagged_version_requires_its_changelog(tmp_path):
    root = make_repo(tmp_path, "0.1.0", "# Changelog\n")
    with pytest.raises(ValueError, match="release heading"):
        release_candidate(root)


def test_prepared_first_release_needs_no_previous_tag(tmp_path):
    root = make_repo(tmp_path, "0.1.0", "# Changelog\n\n## v0.1.0 (2026-10-02)\n")
    result = release_candidate(root)
    assert result["ready"] == "true"
    assert result["tag"] == "v0.1.0"
    assert result["tag_exists"] == "false"


def test_exact_release_commit_can_resume_after_tag_creation(tmp_path):
    root = make_repo(tmp_path, "0.1.0", "## v0.1.0\n")
    subprocess.run(["git", "tag", "-a", "v0.1.0", "-m", "release"], cwd=root, check=True)
    result = release_candidate(root)
    assert result["ready"] == "true"
    assert result["tag_exists"] == "true"


def test_later_commit_does_not_republish_old_version(tmp_path):
    root = make_repo(tmp_path, "0.1.0", "## v0.1.0\n")
    subprocess.run(["git", "tag", "v0.1.0"], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "docs: follow-up"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    assert release_candidate(root)["ready"] == "false"


def test_version_is_a_literal_semver_without_importing_package(tmp_path):
    source = tmp_path / VERSION_FILE
    source.parent.mkdir(parents=True)
    source.write_text('raise RuntimeError("must not import")\n__version__ = "0.2.0"\n')
    assert read_version(tmp_path) == "0.2.0"
    source.write_text('__version__ = "0.0.0.dev0"\n')
    with pytest.raises(ValueError, match="SemVer"):
        read_version(tmp_path)
