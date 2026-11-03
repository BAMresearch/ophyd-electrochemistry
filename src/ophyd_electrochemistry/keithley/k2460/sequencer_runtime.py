"""Packaged sequencer runtime artifact (``tsp/sequencer.tsp``) and its Lua 5.0 lint.

The 2460 runs Lua 5.0.2 (no ``#`` length operator, no ``%`` operator) and
rejects input lines of about 3 KB (bench, 3 Nov 2026). The lint enforces both
on the packaged source; the unit tests also execute it under Lua 5.1 with a
mocked instrument API.
"""

import hashlib
import re
from importlib.resources import files

from .runtime import RuntimeArtifact

SEQUENCER_ABI = "oe-k2460-seq-v1"
SEQUENCER_BUILD = "seq-v1"
SEQUENCER_RESOURCE = "tsp/sequencer.tsp"
MAX_LINE_CHARS = 900
_SCRIPT_DIGEST_PREFIX_LENGTH = 24
_STRING = re.compile(r'"(?:[^"\\]|\\.)*"|' + r"'(?:[^'\\]|\\.)*'")
_FORBIDDEN = (
    ("#", "the '#' length operator does not exist in Lua 5.0"),
    ("%", "the '%' operator does not exist in Lua 5.0"),
    ("//", "integer division does not exist in Lua 5.0"),
    ("goto", "goto does not exist in Lua 5.0"),
)


def lint_lua50(source: str) -> list[str]:
    """Return problems with Lua 5.0 syntax or instrument line limits (empty if none)."""
    problems = []
    for number, line in enumerate(source.splitlines(), start=1):
        if len(line) >= MAX_LINE_CHARS:
            problems.append(f"line {number}: {len(line)} characters (limit {MAX_LINE_CHARS})")
        if not line.isascii():
            problems.append(f"line {number}: non-ASCII character")
        code = _STRING.sub('""', line).split("--", 1)[0]
        for token, reason in _FORBIDDEN:
            if token in code:
                problems.append(f"line {number}: {reason}")
    return problems


def sequencer_packaged_runtime() -> RuntimeArtifact:
    readable = files(__package__).joinpath(SEQUENCER_RESOURCE).read_text(encoding="ascii")
    if not readable.endswith("\n") or "\r" in readable:
        raise RuntimeError("Packaged sequencer runtime must use LF framing and end with LF")
    # Empty lines break script upload on firmware 1.7.16a (see runtime.packaged_runtime).
    source = "\n".join(line for line in readable.splitlines() if line) + "\n"
    if f'oe_seq_runtime_abi = "{SEQUENCER_ABI}"' not in source:
        raise RuntimeError("Packaged sequencer ABI does not match the host adapter")
    if f'oe_seq_runtime_build = "{SEQUENCER_BUILD}"' not in source:
        raise RuntimeError("Packaged sequencer build does not match the host adapter")
    problems = lint_lua50(source)
    if problems:
        raise RuntimeError("Packaged sequencer is not Lua 5.0 clean: " + "; ".join(problems))
    digest = hashlib.sha256(source.encode("ascii")).hexdigest()
    return RuntimeArtifact(
        abi=SEQUENCER_ABI,
        build=SEQUENCER_BUILD,
        source=source,
        sha256=digest,
        script_name=f"oe_seq_{digest[:_SCRIPT_DIGEST_PREFIX_LENGTH]}",
        abi_variable="oe_seq_runtime_abi",
        build_variable="oe_seq_runtime_build",
        initializer="oe_seq_initialize",
    )
