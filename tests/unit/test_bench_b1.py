"""The B1 bench runner compiles its whole matrix offline and refuses unconfirmed loads."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "bench" / "b1_sequencer.py"


@pytest.fixture(scope="module")
def b1():
    spec = importlib.util.spec_from_file_location("b1_sequencer", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dry_run_compiles_every_request(b1, capsys):
    assert b1.main(["--dry-run"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 22
    assert [line.split()[1] for line in lines[11:14]] == ["57", "57", "63"]
    assert lines[0].split()[:2] == ["b1-s1-immediate", "53"]
    assert lines[1].split()[:2] == ["b1-s1-external", "55"]


@pytest.mark.parametrize(
    "argv", [[], ["--load", "resistor"], ["--load", "rc", "--i-confirm-resistor"]]
)
def test_hardware_runs_need_an_explicit_load_confirmation(b1, argv):
    with pytest.raises(SystemExit):
        b1.main(argv)


def test_matrix_names_match_the_plan(b1):
    plan = (SCRIPT.parent / "plan-b1.md").read_text()
    for name in b1.TESTS:
        assert f"| {name} |" in plan
