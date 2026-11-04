"""The B2 sheets validate against their sessions; the monitor summarizes offline files."""

import importlib.util
import json
from pathlib import Path

import pytest

pytest.importorskip("openpyxl")
pytest.importorskip("plotly")

BENCH = Path(__file__).parents[2] / "bench"


def load(name):
    spec = importlib.util.spec_from_file_location(name, BENCH / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def sheets():
    return load("b2_sheets")


@pytest.fixture(scope="module")
def monitor():
    return load("b2_monitor")


@pytest.mark.parametrize("capacity", ["0.3", "0.45", "0.6"])
def test_every_row_validates_against_its_session(sheets, tmp_path, capsys, capacity):
    assert sheets.main(["--capacity-mah", capacity, "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "FAIL" not in out
    assert sorted(p.name for p in tmp_path.glob("*.xlsx")) == sorted(sheets.SHEETS)


def test_sheets_are_never_overwritten_by_default(sheets, tmp_path, capsys):
    assert sheets.main(["--out", str(tmp_path)]) == 0
    with pytest.raises(FileExistsError):
        sheets.main(["--out", str(tmp_path)])


def test_capacity_is_checked(sheets):
    with pytest.raises(SystemExit):
        sheets.main(["--capacity-mah", "50"])


def test_rows_match_the_plan_and_cell_currents_scale(sheets):
    plan = (BENCH / "plan-b2.md").read_text()
    wide, measure, cycle = sheets.cell_rows(0.5, "cell", 20)
    for row in sheets.resistor_rows() + wide + measure + cycle:
        name = str(row["measurement_id"])
        if name.startswith("k2-"):
            name = "k2-<dir>-<n>C"  # the rate check is described as a series
        elif name.startswith(("k4-", "k5-")):
            name = name.replace("20C", "<n>C")
        assert f"`{name}" in plan, name
    currents = {row["measurement_id"]: row.get("current_a") for row in wide + measure}
    assert currents["k2-sod-40C"] == pytest.approx(-40 * 0.5e-3)
    assert currents["k3-sodiate-5mV"] == pytest.approx(-0.5e-3 / 10)
    assert currents["k4-desod-1s-20C"] == pytest.approx(20 * 0.5e-3)
    assert currents["k5-sod-1s-20C"] == pytest.approx(-20 * 0.5e-3)
    # Unipolar 1 s pulses that end at a voltage limit; repeats bound 1.5x the capacity.
    assert {row["protocol"] for row in measure} == {"pulse_train"}
    assert all(row["pulse_s"] == 1 and row["rest_s"] == 60 for row in measure)
    assert all(row["repeats"] == 270 for row in measure)
    assert all(("v_low" in row) != ("v_high" in row) for row in measure)
    # Nothing cuts below 5 mV; the rate check stays away from plating.
    assert all(row.get("v_low", 0.005) >= 0.005 for row in wide + measure + cycle)
    assert all(row.get("v_low", 0.05) >= 0.05 for row in wide)


def test_pulse_rate_is_checked(sheets):
    with pytest.raises(SystemExit):
        sheets.main(["--pulse-c-rate", "2"])


def test_run_checks_and_evidence(monitor, viewer_runs, tmp_path, monkeypatch):
    directory, paths = viewer_runs
    checks = monitor.run_checks(paths["stopped_cutoff"])
    assert checks["outcome"] == "stopped_cutoff"
    assert checks["median_v_over_i_ohm"] > 0
    assert any("cutoff" in event for event in checks["events"])
    assert monitor.run_checks(paths["completed"])["rest_v_first_last"] is not None

    csv_path = tmp_path / "monitor.csv"
    monkeypatch.setattr(monitor, "processes", lambda: [])
    monitor.watch(directory, csv_path, 0.01, count=2)
    monkeypatch.setattr(monitor, "EVIDENCE", tmp_path / "evidence")
    out = monitor.evidence("sim", [directory], csv_path, "test")
    result = json.loads(out.read_text())
    assert out.name.endswith("-b2-sim-test.json")
    outcomes = {run["outcome"] for run in result["runs"]}
    assert {"completed", "stopped_cutoff", "aborted", "incomplete", "unreadable"} <= outcomes
    assert result["monitor"]["samples"] == 2
    # The newest file is the corrupt one: the watch logs it and carries on.
    assert "error:" in csv_path.read_text()


def test_processes_finds_only_re_manager_trees(monitor):
    assert all("start-re-manager" not in p["command"] or p["pid"] for p in monitor.processes())
