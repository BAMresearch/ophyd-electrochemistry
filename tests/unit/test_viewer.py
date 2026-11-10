"""Read-only discovery, signed charge, pulse-preserving plots and live follow."""

import hashlib
import threading

import numpy as np
import pytest

from ophyd_electrochemistry.exceptions import ValidationError
from ophyd_electrochemistry.sequences.run_file import read_run_file
from ophyd_electrochemistry.sequences.viewer import (
    charge_mah,
    decimate,
    follow,
    latest_run,
    overlay_figure,
    run_figure,
    run_index,
)


@pytest.fixture(autouse=True)
def plotly_renderer(monkeypatch):
    monkeypatch.setattr("plotly.io.renderers.default", "plotly_mimetype")


def test_index_values_order_and_corrupt_isolation(viewer_runs):
    directory, paths = viewer_runs
    frame = run_index(str(directory))
    assert list(frame.columns) == [
        "file",
        "measurement_id",
        "sample_id",
        "protocol",
        "start_utc",
        "duration_s",
        "records",
        "outcome",
        "cutoffs",
        "stops",
        "aborts",
        "errors",
        "detail",
    ]
    assert frame.file.tolist() == list(reversed(paths.values()))
    assert frame.outcome.tolist() == list(reversed(paths))
    rows = frame.set_index("outcome")
    completed = rows.loc["completed"]
    saved = read_run_file(paths["completed"])
    assert completed.measurement_id == "completed"
    assert completed.sample_id == "cell-01"
    assert completed.protocol == "rest"
    assert completed.start_utc == saved["run"]["time_origin_utc"]
    assert completed.duration_s == saved["readout"]["t_s"][-1]
    assert completed.records == len(saved["readout"]["t_s"])
    assert completed[["cutoffs", "stops", "aborts", "errors"]].tolist() == [0, 0, 0, 0]
    assert rows.loc["stopped_cutoff", "cutoffs"] == 1
    assert rows.loc["stopped_cutoff", "stops"] == 1
    assert rows.loc["stopped_cutoff", "detail"] == "v_max"
    assert rows.loc["aborted", "aborts"] == 1
    assert rows.loc["aborted", "records"] > 0
    assert rows.loc["incomplete", "records"] == 3
    assert rows.loc["incomplete", "duration_s"] == 0.3
    assert rows.loc["incomplete", "errors"] == 1
    assert rows.loc["unreadable", "detail"]
    assert latest_run(directory) == paths["unreadable"]


def test_empty_directory_and_time_origin_fallback(tmp_path, viewer_writer):
    assert run_index(tmp_path / "missing").empty
    assert latest_run(tmp_path / "missing") is None
    path, _, _ = viewer_writer
    row = run_index(path.parent).iloc[0]
    assert row.outcome == "incomplete"
    assert row.start_utc == read_run_file(path)["run"]["started_utc"]


@pytest.mark.parametrize("sign", [1, -1])
def test_charge_constant_current(sign):
    readout = {"t_s": np.array([10, 1810, 3610]), "current_a": np.full(3, sign * 0.001)}
    np.testing.assert_allclose(charge_mah(readout), [0, sign * 0.5, sign])


def test_charge_trapezoids_variable_sampling_and_reversal():
    readout = {"t_s": np.array([2, 3, 5, 9]), "current_a": np.array([0, 0.002, 0, -0.004])}
    np.testing.assert_allclose(charge_mah(readout), np.array([0, 0.001, 0.003, -0.005]) / 3.6)


def test_charge_empty_and_single():
    assert charge_mah({"t_s": np.array([]), "current_a": np.array([])}).size == 0
    np.testing.assert_array_equal(
        charge_mah({"t_s": np.array([5]), "current_a": np.array([1])}), [0]
    )


@pytest.mark.parametrize("budget", [2, 3, 4, 5, 10, 99, 1000])
def test_decimate_budget_sorted_unique_endpoints(budget):
    x = np.arange(1000)
    y = np.random.default_rng(4).normal(size=1000)
    indices = decimate(x, y, budget)
    assert len(indices) <= budget
    assert indices[0] == 0 and indices[-1] == 999
    assert np.all(np.diff(indices) > 0)
    assert np.issubdtype(indices.dtype, np.integer)


@pytest.mark.parametrize("sign", [1, -1])
def test_decimate_single_sample_pulse_survives_million_samples(sign):
    x = np.linspace(0, 36000, 10**6)
    y = np.zeros(len(x))
    y[123457] = sign
    indices = decimate(x, y, 200)
    assert 123457 in indices
    assert len(indices) <= 200


def test_decimate_preserves_bin_min_and_max():
    x = np.arange(1000)
    y = np.random.default_rng(2).normal(size=1000)
    indices = decimate(x, y, 22)
    for start in range(0, 1000, 100):
        assert start + np.argmin(y[start : start + 100]) in indices
        assert start + np.argmax(y[start : start + 100]) in indices


@pytest.mark.parametrize("count", [0, 1, 10])
def test_decimate_short_data(count):
    x = np.arange(count)
    np.testing.assert_array_equal(decimate(x, x, 10), x)


@pytest.mark.parametrize("budget", [0, 1, -1, True, 3.5])
def test_decimate_rejects_impossible_or_invalid_budget(budget):
    with pytest.raises(ValidationError, match="max_points"):
        decimate(np.arange(10), np.arange(10), budget)


def test_decimate_rejects_mismatched_arrays():
    with pytest.raises(ValueError, match="equal-length"):
        decimate(np.arange(2), np.arange(3), 10)


@pytest.mark.parametrize(
    "axis,label", [("time", "Time since start (s)"), ("charge", "Charge (mAh)")]
)
def test_run_and_overlay_figures(viewer_runs, axis, label):
    _, paths = viewer_runs
    fig = run_figure(paths["completed"], x=axis, max_points=4)
    assert len(fig.data) == 2
    assert "completed — completed" in fig.layout.title.text
    assert "10 records" in fig.layout.title.text
    assert fig.layout.xaxis2.title.text == label
    assert fig.layout.yaxis.title.text == "Voltage (V)"
    assert fig.layout.yaxis2.title.text == "Current (A)"
    assert all(2 <= len(trace.x) <= 4 for trace in fig.data)
    if axis == "charge":
        assert fig.data[0].x[0] == 0
    overlay = overlay_figure([paths["completed"], paths["stopped_cutoff"]], x=axis, max_points=4)
    assert len(overlay.data) == 2
    assert overlay.layout.xaxis.title.text == label
    assert overlay.layout.yaxis.title.text == "Voltage (V)"
    assert overlay.data[0].name == "completed (cell-01)"
    assert overlay.data[1].name == "stopped_cutoff"
    assert all(trace.x[0] == 0 and len(trace.x) <= 4 for trace in overlay.data)


def test_full_resolution_and_charge_cutoff_markers(viewer_runs):
    _, paths = viewer_runs
    saved = read_run_file(paths["stopped_cutoff"])
    fig = run_figure(paths["stopped_cutoff"], x="charge", max_points=100000)
    np.testing.assert_array_equal(fig.data[0].x, charge_mah(saved["readout"]))
    np.testing.assert_array_equal(fig.data[0].y, saved["readout"]["voltage_v"])
    assert len(fig.layout.shapes) == 4
    assert all(shape.x0 == fig.data[0].x[-1] for shape in fig.layout.shapes)


@pytest.mark.parametrize("function,args", [(run_figure, "file"), (overlay_figure, "list")])
def test_invalid_axis(viewer_runs, function, args):
    _, paths = viewer_runs
    path = paths["completed"]
    with pytest.raises(ValueError, match="x must be"):
        function(path if args == "file" else [path], x="invalid")


class Display:
    def __init__(self):
        self.calls = []

    def __call__(self, bundle, **kwargs):
        self.calls.append(("display", bundle, kwargs))
        return self

    def update(self, bundle, **kwargs):
        self.calls.append(("update", bundle, kwargs))


def test_follow_writer_thread_appends_and_finalizes(viewer_writer):
    path, writer, chunk = viewer_writer
    display = Display()
    advance, written = threading.Event(), threading.Event()
    failures = []

    def write():
        try:
            for part in range(2):
                assert advance.wait(5)
                advance.clear()
                writer.append_readout(dict(chunk, t_s=chunk["t_s"] + (part + 1) * 0.2))
                if part == 1:
                    writer.finalize("completed")
                written.set()
        except BaseException as exc:
            failures.append(exc)
            written.set()

    def sleep(period):
        assert period == 2
        written.clear()
        advance.set()
        assert written.wait(5)
        if failures:
            raise failures[0]

    thread = threading.Thread(target=write)
    thread.start()
    try:
        assert follow(path, display=display, sleep=sleep, timeout_s=10) == "completed"
    finally:
        advance.set()
        thread.join(5)
    assert not thread.is_alive()
    assert not failures
    assert [call[0] for call in display.calls] == ["display", "update", "update"]
    assert display.calls[0][2] == {"raw": True, "display_id": True}
    assert all(call[2] == {"raw": True} for call in display.calls[1:])
    bundles = [call[1]["application/vnd.plotly.v1+json"] for call in display.calls]
    assert "2 records" in bundles[0]["layout"]["title"]["text"]
    assert "6 records" in bundles[-1]["layout"]["title"]["text"]


def test_follow_timeout_returns_incomplete(viewer_writer, monkeypatch):
    path, _, _ = viewer_writer
    clock = [0.0]
    sleeps = []
    monkeypatch.setattr("ophyd_electrochemistry.sequences.viewer.time.monotonic", lambda: clock[0])

    def sleep(period):
        sleeps.append(period)
        clock[0] += period

    display = Display()
    assert follow(path, period_s=2, timeout_s=3, display=display, sleep=sleep) == "incomplete"
    assert sleeps == [2, 1]
    assert [call[0] for call in display.calls] == ["display", "update", "update"]


def test_follow_keyboard_interrupt_and_default_display(viewer_writer, monkeypatch):
    path, _, _ = viewer_writer
    display = Display()
    monkeypatch.setattr("IPython.display.display", display)

    def interrupt(period):
        raise KeyboardInterrupt

    assert follow(path, sleep=interrupt) == "incomplete"
    assert len(display.calls) == 1


@pytest.mark.parametrize("outcome", ["completed", "stopped_cutoff", "aborted"])
def test_follow_finished_file_returns_immediately_and_leaves_bytes_intact(viewer_runs, outcome):
    directory, paths = viewer_runs
    before = {path: hashlib.sha256(path.read_bytes()).digest() for path in paths.values()}

    def no_sleep(period):
        pytest.fail("a finished run must not sleep")

    display = Display()
    assert follow(paths[outcome], display=display, sleep=no_sleep) == outcome
    run_index(directory)
    overlay_figure([paths[outcome]])
    assert len(display.calls) == 1
    assert before == {path: hashlib.sha256(path.read_bytes()).digest() for path in paths.values()}


def test_empty_file_before_first_sample(viewer_writer, tmp_path):
    from ophyd_electrochemistry.sequences.models import limits_from_dict, sequence_from_dict
    from ophyd_electrochemistry.sequences.run_file import RunFile

    source, _, _ = viewer_writer
    saved = read_run_file(source)
    path = tmp_path / "empty.h5"
    with RunFile.create(
        path,
        measurement_id="empty",
        equipment={},
        sequence=sequence_from_dict(saved["sequence"]),
        limits=limits_from_dict(saved["safety_limits"]),
        start_mode="external",
    ):
        row = run_index(tmp_path).set_index("file").loc[path]
        assert row.duration_s == 0
        assert row.records == 0
        assert row.outcome == "incomplete"
        for axis in ("time", "charge"):
            fig = run_figure(path, x=axis)
            assert len(fig.data) == 2
            assert all(len(trace.x) == 0 for trace in fig.data)
            overlay = overlay_figure([path], x=axis)
            assert len(overlay.data) == 1 and len(overlay.data[0].x) == 0
        assert follow(path, timeout_s=0, display=Display()) == "incomplete"


def test_trace_extrema_and_charge_use_full_saved_data(viewer_writer):
    path, writer, chunk = viewer_writer
    count = 10000
    times = np.arange(1, count + 1) * 0.1 + chunk["t_s"][-1]
    voltage, current = np.zeros(count), np.zeros(count)
    voltage[1234], current[5678] = 3, -0.001
    writer.append_readout(
        {
            key: times
            if key == "t_s"
            else voltage
            if key == "voltage_v"
            else current
            if key == "current_a"
            else np.zeros(count)
            for key in chunk
        }
    )
    fig = run_figure(path, max_points=100)
    assert 3 in fig.data[0].y
    assert -0.001 in fig.data[1].y
    saved = read_run_file(path)
    fig = run_figure(path, x="charge", max_points=100)
    assert fig.data[0].x[-1] == pytest.approx(charge_mah(saved["readout"])[-1])
    assert fig.data[0].x[-1] == pytest.approx(-0.001 * 0.1 / 3.6)
    overlay = overlay_figure([path], max_points=100)
    assert 3 in overlay.data[0].y
    assert overlay.data[0].x[-1] == fig.data[0].x[-1]
