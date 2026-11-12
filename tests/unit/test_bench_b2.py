"""The B2 sheets validate against their sessions; the monitor summarizes offline files."""

import importlib.util
import json
from pathlib import Path

import numpy as np
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


@pytest.fixture(scope="module")
def explore():
    return load("explore")


TRAIN = dict(
    measurement_id="x",
    protocol="pulse_train",
    current_a=0.00048,
    pulse_s=1,
    rest_s=10,
    repeats=60,
    v_high=2.9,
)


@pytest.mark.parametrize(
    ("change", "net", "elapsed", "message"),
    [
        ({"current_a": 0.0193}, 0, 0, "above"),
        ({"current_a": -0.0193}, 0, 0, "above"),
        ({"pulse_s": 0.01}, 0, 0, "outside"),
        ({"pulse_s": 61}, 0, 0, "outside"),
        ({"rest_s": 601}, 0, 0, "outside"),
        ({"pre_rest_s": 0.5}, 0, 0, "outside"),
        ({"protocol": "cycling"}, 0, 0, "protocol"),
        ({"start_mode": "external"}, 0, 0, "immediate"),
        ({"sample_id": "other"}, 0, 0, "sample"),
        ({"current_a": 0.0048, "pulse_s": 60}, 0, 0, "one run"),
        ({"repeats": 60}, 33, 0, "net charge"),
        ({"current_a": -0.00048}, -33, 0, "net charge"),
        ({}, 0, 7000, "series limit"),
    ],
)
def test_explore_refuses_rows_outside_the_authorization(explore, change, net, elapsed, message):
    with pytest.raises(explore.Refused, match=message):
        explore.check_row({**TRAIN, **change}, net, elapsed, 670)


def test_explore_accepts_the_edges_of_the_authorization(explore):
    edge = {**TRAIN, "current_a": -0.0048, "pulse_s": 1, "rest_s": 600, "repeats": 29}
    explore.check_row(edge, 0, 0, 3600)
    explore.check_row({**TRAIN, "protocol": "bipolar_pulse"}, 30, 0, 670)
    with pytest.raises(explore.Refused, match="v_high"):
        explore.check_row({**TRAIN, "v_high": 3.0}, 0, 0, 670)
    assert explore.run_charge_uah(TRAIN) == pytest.approx((8, 8))
    assert explore.run_charge_uah({**TRAIN, "protocol": "bipolar_pulse"}) == pytest.approx((16, 0))


def test_explore_stop_rules(explore):
    t = np.arange(0, 3, 0.1)
    i = np.where((t > 1) & (t < 2), 1e-3, 0.0)
    v = np.where(i > 0, 1.0, 0.5)
    data = {
        "run": {"outcome": "completed"},
        "attrs": {"measurement_id": "ref-02"},
        "readout": {"t_s": t, "voltage_v": v, "current_a": i},
    }
    assert explore.assess(data, 500) == []
    assert "reference" in explore.assess(data, 1500)[0]
    shorted = {**data, "readout": {**data["readout"], "voltage_v": np.where(i > 0, 0.51, 0.5)}}
    assert "short" in explore.assess(shorted, None)[0]
    assert "outcome" in explore.assess({**data, "run": {"outcome": "error"}}, None)[0]
    negative = {**data, "readout": {**data["readout"], "voltage_v": v - 0.6}}
    assert "negative" in explore.assess(negative, None)[0]


def test_explore_pulses_survive_a_first_sample_and_a_cutoff(explore):
    t = np.arange(0, 3, 0.1)
    i = np.where(t > 2, 1e-3, 0.0)  # the run ends inside the pulse
    i[0] = 5e-6  # output-on reading at 0 A
    v = np.where(i > 1e-4, 1.5, 0.5)
    data = {
        "run": {"outcome": "stopped_cutoff"},
        "attrs": {"measurement_id": "x"},
        "readout": {"t_s": t, "voltage_v": v, "current_a": i},
    }
    (pulse,) = explore.pulses(data)
    assert pulse["i_a"] == pytest.approx(1e-3)
    assert pulse["r_end_ohm"] == pytest.approx(1000)
    assert explore.assess(data, None) == []


B3_TRAIN = dict(
    measurement_id="x",
    protocol="pulse_train",
    sample_id="AlCe Test Cell 1",
    current_a=0.0104,
    pulse_s=0.5,
    rest_s=10,
    repeats=3,
    v_high=2.9,
    v_low=-2.9,
)


@pytest.fixture
def explore_b3(explore, monkeypatch):
    monkeypatch.setattr(explore, "CFG", explore.SERIES["b3"])
    return explore


@pytest.mark.parametrize(
    ("change", "net", "message"),
    [
        ({"current_a": 0.0105}, 0, "above"),
        ({"pulse_s": 1, "current_a": 0.0014}, 0, "above"),  # 5C from 1 s on
        ({"pulse_s": 121, "current_a": 0.001}, 0, "outside"),
        ({"current_a": -0.0104}, -14, "net charge"),  # starts sodiated
        ({"pulse_s": 60, "current_a": 0.00026, "repeats": 15}, 140, "net charge"),
        ({"pulse_s": 60, "current_a": 0.0013, "repeats": 4}, 0, "one run"),
        ({"sample_id": "Richard-HC400-rev"}, 0, "sample"),
        ({"v_high": 3.0}, 0, "v_high"),
        ({"v_low": -3.0}, 0, "v_low"),
        ({"v_low": None}, 0, "v_low"),
    ],
)
def test_explore_b3_refuses_rows_outside_its_page(explore_b3, change, net, message):
    with pytest.raises(explore_b3.Refused, match=message):
        explore_b3.check_row({**B3_TRAIN, **change}, net, 0, 60)


def test_explore_b3_accepts_its_planned_rows(explore_b3):
    explore_b3.check_row(B3_TRAIN, 0, 0, 60)  # 40C, 0.5 s
    ici = {**B3_TRAIN, "current_a": 0.00026, "pulse_s": 60, "repeats": 15}
    explore_b3.check_row(ici, 0, 0, 1100)  # 65 uAh desodiation
    explore_b3.check_row({**ici, "current_a": -0.00026}, 65, 0, 1100)
    # bipolar, desodiation first: -14.5 -> -13.5 -> -14.5 uAh never reaches -15.5
    explore_b3.check_row(
        {**B3_TRAIN, "protocol": "bipolar_pulse", "current_a": 0.0072}, -14.5, 0, 60
    )
    from ophyd_electrochemistry.sequences.queueserver import load_session

    limits = load_session(explore_b3.CFG.session).limits
    assert (limits.v_min_abs, limits.v_max_abs, limits.i_max_abs) == (-3.0, 3.0, 0.0104)


def test_explore_references_compare_only_at_a_similar_voltage(explore, tmp_path, monkeypatch):
    tables = {"ref-01d_1.h5": 0.10, "ref-02d_2.h5": 0.40}
    for name in tables:
        (tmp_path / name).touch()
    monkeypatch.setattr(explore, "read_run_file", lambda path: path.name)
    monkeypatch.setattr(
        explore,
        "pulses",
        lambda name: [{"v_pre": tables[name], "r_end_ohm": 1000.0 * (1 + tables[name])}],
    )
    assert explore.reference_ohm(tmp_path, "d") == pytest.approx(1100)
    assert explore.reference_ohm(tmp_path, "d", 0.42) == pytest.approx(1400)
    assert explore.reference_ohm(tmp_path, "d", 0.25) is None
    # the newest reference is never its own baseline (brief 14)
    newest = tmp_path / "ref-02d_2.h5"
    assert explore.reference_ohm(tmp_path, "d", 0.40, exclude=newest) is None
