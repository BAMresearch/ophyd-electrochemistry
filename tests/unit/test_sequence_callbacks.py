"""HDF5 and plotting subscribers, including failures before any source is enabled."""

import math
from dataclasses import replace
from datetime import UTC, datetime

import numpy as np
import pytest
from bluesky import RunEngine
from event_model import unpack_event_page

from ophyd_electrochemistry.sequences import (
    CutoffAction,
    SafetyLimits,
    Sampling,
    Sequence,
    SequenceFlyer,
    SequenceRequest,
    SimulatedSequenceBackend,
    bipolar_pulse_sequence,
    cc_step,
    run_sequence,
)
from ophyd_electrochemistry.sequences.callbacks import (
    LiveSequencePlot,
    RunFileWriter,
    plot_run_file,
)
from ophyd_electrochemistry.sequences.run_file import READOUT_FIELDS, RunFile, read_run_file
from ophyd_electrochemistry.sequences.simulator import simulate_sequence
from ophyd_electrochemistry.state import DeviceState


@pytest.fixture
def seq_request():
    return SequenceRequest(
        "pulse / 001",
        bipolar_pulse_sequence(0.001, 0.1, 2),
        SafetyLimits(0, 4, 0.01, 10000, 100000),
        sample_id="cell-01",
        notes="Na / hard carbon",
        run_sheet_row={"measurement_id": "pulse / 001", "operator": "Béa", "custom": None},
    )


def execute(seq_request, *subscribers):
    backend = SimulatedSequenceBackend(speed=math.inf, seed=7)
    flyer = SequenceFlyer(backend, name="sequence", poll_period_s=0.002)
    re = RunEngine({}, call_returns_result=True)
    docs = []
    re.subscribe(lambda n, d: docs.append((n, d)))
    for subscriber in subscribers:
        re.subscribe(subscriber)
    result = re(run_sequence(flyer, seq_request))
    return result.plan_result, docs, backend


def test_writer_full_roundtrip_and_static_plot(tmp_path, seq_request):
    writer = RunFileWriter(tmp_path)
    outcome, docs, backend = execute(seq_request, writer)
    assert outcome == "completed"
    assert writer.path.parent == tmp_path
    assert writer.path.name.startswith("pulse___001_")
    assert writer.path.name.endswith("Z.h5")
    saved = read_run_file(writer.path)
    expected = simulate_sequence(seq_request.sequence, seq_request.limits, seed=7)
    assert saved["equipment"] == backend.equipment()
    assert saved["run_sheet_row"] == seq_request.run_sheet_row
    assert saved["run"]["sample_id"] == seq_request.sample_id
    assert saved["run"]["notes"] == seq_request.notes
    assert saved["run"]["outcome"] == "completed"
    origin = backend.time_origin_epoch_s()
    assert saved["run"]["time_origin_epoch_s"] == pytest.approx(origin, abs=5e-7, rel=0)
    assert saved["run"]["time_origin_utc"] == datetime.fromtimestamp(
        saved["run"]["time_origin_epoch_s"], UTC
    ).isoformat(timespec="microseconds").replace("+00:00", "Z")
    for key in READOUT_FIELDS:
        np.testing.assert_array_equal(saved["readout"][key], expected.readout[key])
    assert list(zip(*saved["events"].values(), strict=True)) == expected.events
    fig = plot_run_file(writer.path)
    np.testing.assert_array_equal(fig.data[0].x, expected.readout["t_s"])
    np.testing.assert_array_equal(fig.data[0].y, expected.readout["voltage_v"])
    np.testing.assert_array_equal(fig.data[1].y, expected.readout["current_a"])
    assert "completed" in fig.layout.title.text
    assert len(docs) >= 6


def test_programmed_cutoff_returns_success_and_is_persisted(tmp_path, seq_request):
    sequence = Sequence((cc_step(0.001, 0.11, 10, Sampling(0.1), CutoffAction.STOP),))
    request = replace(seq_request, sequence=sequence)
    writer = RunFileWriter(tmp_path)
    outcome, docs, backend = execute(request, writer)
    assert outcome == "stopped_cutoff"
    assert docs[-1][1]["exit_status"] == "success"
    assert backend.inspect() == (DeviceState.COMPLETE, False)
    saved = read_run_file(writer.path)
    assert saved["run"]["outcome"] == "stopped_cutoff"
    assert saved["events"]["kind"].tolist() == ["step_start", "cutoff", "stop"]
    assert saved["run"]["outcome_detail"] == "v_max"
    fig = plot_run_file(writer.path)
    assert len(fig.layout.shapes) == 4  # cutoff and stop on both axes
    assert any("cutoff" in a.text for a in fig.layout.annotations)


@pytest.mark.parametrize("existing", [False, True])
def test_writer_creation_error_prevents_kickoff(tmp_path, seq_request, existing):
    class CountingBackend(SimulatedSequenceBackend):
        kickoffs = 0

        def kickoff(self):
            self.kickoffs += 1
            super().kickoff()

    backend = CountingBackend(speed=math.inf)
    flyer = SequenceFlyer(backend, name="sequence")
    directory = tmp_path
    if not existing:
        directory = tmp_path / "not-a-directory"
        directory.write_text("keep")
    writer = RunFileWriter(directory)
    re = RunEngine({})
    collision = []

    def reserve_path(name, doc):
        if name == "start" and existing:
            stamp = datetime.fromtimestamp(doc["time"], UTC).strftime("%Y%m%dT%H%M%SZ")
            path = tmp_path / f"pulse___001_{stamp}.h5"
            path.write_bytes(b"retained data")
            collision.append(path)

    re.subscribe(reserve_path)
    re.subscribe(writer)
    with pytest.raises((FileExistsError, NotADirectoryError)):
        re(run_sequence(flyer, seq_request))
    assert backend.kickoffs == 0
    assert backend.inspect() == (DeviceState.PREPARED, False)
    assert writer._file is None
    if existing:
        assert collision[0].read_bytes() == b"retained data"
    else:
        assert directory.read_text() == "keep"
    # The never-started sequence must not block the next run (no kernel restart).
    retry = RunEngine({})
    retry.subscribe(RunFileWriter(tmp_path / "retry"))
    retry(run_sequence(flyer, seq_request))
    assert backend.kickoffs == 1
    assert flyer.outcome == "completed"


def test_single_event_documents_and_other_runs_are_ignored(tmp_path, seq_request):
    _, docs, _ = execute(seq_request)
    writer = RunFileWriter(tmp_path)
    writer("start", {"plan_name": "something_else"})
    assert writer.path is None
    for name, doc in docs:
        if name == "event_page":
            for event in unpack_event_page(doc):
                writer("event", event)
        else:
            writer(name, doc)
    saved = read_run_file(writer.path)
    assert saved["run"]["outcome"] == "completed"
    assert len(saved["readout"]["t_s"]) == docs[0][1]["expected_records"]
    assert saved["events"]["kind"].tolist().count("step_start") == 9


@pytest.mark.parametrize("status,outcome", [("abort", "aborted"), ("fail", "error")])
def test_stop_failure_outcome_and_reason(tmp_path, seq_request, status, outcome):
    _, docs, _ = execute(seq_request)
    writer = RunFileWriter(tmp_path)
    for name, doc in docs:
        if name == "stop":
            doc = dict(doc, exit_status=status, reason="operator reason")
        writer(name, doc)
    saved = read_run_file(writer.path)
    assert saved["run"]["outcome"] == outcome
    assert saved["run"]["outcome_detail"] == "operator reason"
    assert writer._file is None


@pytest.mark.parametrize(
    "method", ["append_readout", "append_event", "set_time_origin", "finalize"]
)
def test_writer_closes_file_on_callback_error(tmp_path, seq_request, monkeypatch, method):
    _, docs, _ = execute(seq_request)
    writer = RunFileWriter(tmp_path)

    def fail(*args, **kwargs):
        raise RuntimeError("disk failure")

    monkeypatch.setattr(RunFile, method, fail)
    with pytest.raises(RuntimeError, match="disk failure"):
        for name, doc in docs:
            writer(name, doc)
    assert writer._file is None
    assert read_run_file(writer.path)["run"]["outcome"] == "incomplete"


def test_live_plot_throttle_final_redraw_and_decimation(seq_request, monkeypatch):
    _, docs, _ = execute(seq_request)
    now = [100.0]
    monkeypatch.setattr("ophyd_electrochemistry.sequences.callbacks.time.monotonic", lambda: now[0])
    rendered = []
    plot = LiveSequencePlot(title="Cell", render=rendered.append, min_redraw_s=2, max_points=17)
    for name, doc in docs:
        plot(name, doc)
        now[0] += 0.1
    assert len(rendered) == 2  # first data page and forced stop
    assert "Cell: pulse / 001" in rendered[-1].layout.title.text
    assert "completed" in rendered[-1].layout.title.text
    assert 0 < len(rendered[-1].data[0].x) <= 17
    assert len(rendered[-1].data[1].x) <= 17
    assert len(plot._readout["t_s"]) == docs[0][1]["expected_records"]
    assert not rendered[-1].layout.shapes  # step_start markers are not drawn
    unthrottled = []
    plot = LiveSequencePlot(render=unthrottled.append, min_redraw_s=0)
    for name, doc in docs:
        plot(name, doc)
    assert len(unthrottled) == 3
    assert len(unthrottled[-1].data[0].x) == docs[0][1]["expected_records"]


def test_live_plot_cutoff_markers_and_outcome(seq_request):
    request = replace(
        seq_request,
        sequence=Sequence((cc_step(0.001, 0.11, 10, Sampling(0.1), CutoffAction.STOP),)),
    )
    rendered = []
    plot = LiveSequencePlot(render=rendered.append, min_redraw_s=10000)
    execute(request, plot)
    assert "stopped_cutoff" in rendered[-1].layout.title.text
    assert any("cutoff: v_max" in a.text for a in rendered[-1].layout.annotations)
    assert len(rendered[-1].layout.shapes) == 4
