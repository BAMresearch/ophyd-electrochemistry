"""Read/validate release metadata without importing the package or using the network."""

import argparse
import ast
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = Path("src/ophyd_electrochemistry/__init__.py")


def read_version(root: Path = ROOT) -> str:
    tree = ast.parse((root / VERSION_FILE).read_text(encoding="utf-8"))
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in statement.targets
        ):
            version = ast.literal_eval(statement.value)
            if isinstance(version, str) and re.fullmatch(r"\d+\.\d+\.\d+", version):
                return version
    raise ValueError("Expected a literal three-part SemVer __version__")


def release_candidate(root: Path = ROOT) -> dict[str, str]:
    version = read_version(root)
    tag = f"v{version}"
    # 0.0.0 initializes PSR without pretending this template was released.
    if version == "0.0.0":
        return {"ready": "false", "tag": tag, "version": version}
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    existing = subprocess.run(
        ["git", "rev-parse", "--verify", f"refs/tags/{tag}^{{commit}}"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    tag_exists = existing.returncode == 0
    # A rerun of the tested release commit may finish a failed release/publish.
    # Later ordinary commits with the same version must not publish again.
    if tag_exists and existing.stdout.strip() != head:
        return {"ready": "false", "tag": tag, "version": version}
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    heading = rf"(?m)^##\s+(?:v|\[)?{re.escape(version)}(?:\]|\s|$)"
    if not re.search(heading, changelog):
        raise ValueError(f"No changelog release heading for {version}; merge its release PR first")
    return {
        "ready": "true",
        "tag": tag,
        "version": version,
        "tag_exists": "true" if tag_exists else "false",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["version", "candidate"])
    arguments = parser.parse_args()
    if arguments.command == "version":
        print(read_version())
        return
    values = release_candidate()
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as stream:
            for key, value in values.items():
                stream.write(f"{key}={value}\n")
    else:
        for key, value in values.items():
            print(f"{key}={value}")


if __name__ == "__main__":
    main()
