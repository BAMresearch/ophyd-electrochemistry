"""Offline run files shared by the viewer unit and notebook contract tests."""

import math
import os
import threading

import numpy as np
import pytest
from bluesky import RunEngine
from bluesky.utils import RunEngineInterrupted

from ophyd_electrochemistry.sequences import (
    CutoffAction,
    SafetyLimits,
    Sampling,
    Sequence,
    SequenceFlyer,
    SequenceRequest,
    SimulatedSequenceBackend,
    cc_step,
    rest_step,
    run_sequence,
)
from ophyd_electrochemistry.sequences.callbacks import RunFileWriter
from ophyd_electrochemistry.sequences.run_file import READOUT_FIELDS, RunFile


@pytest.fixture
def viewer_writer(tmp_path):
    path = tmp_path / "live.h5"
    with RunFile.create(
        path,
        measurement_id="live",
        equipment={},
        sequence=Sequence((rest_step(10, Sampling(0.1)),)),
        limits=SafetyLimits(0, 4, 0.01, 10000, 100000),
        start_mode="immediate",
        run_sheet_row={"protocol": "rest"},
    ) as writer:
        chunk = {key: np.zeros(2) for key in READOUT_FIELDS}
        chunk.update(t_s=np.array([0.1, 0.2]), voltage_v=np.array([0.1, 0.2]))
        writer.append_readout(chunk)
        yield path, writer, chunk


@pytest.fixture(scope="module")
def viewer_runs(tmp_path_factory):
    directory = tmp_path_factory.mktemp("viewer-runs")
    limits = SafetyLimits(0, 4, 0.01, 10000, 100000)
    paths = {}
    for name in ("completed", "stopped_cutoff", "aborted"):
        backend = SimulatedSequenceBackend(speed=10 if name == "aborted" else math.inf)
        sequence = Sequence((rest_step(30 if name == "aborted" else 1, Sampling(0.1)),))
        if name == "stopped_cutoff":
            sequence = Sequence((cc_step(0.001, 0.11, 10, Sampling(0.1), CutoffAction.STOP),))
        request = SequenceRequest(
            name,
            sequence,
            limits,
            sample_id="cell-01" if name == "completed" else None,
            run_sheet_row={"protocol": "rest" if name != "stopped_cutoff" else "cycling"},
        )
        flyer = SequenceFlyer(backend, name="sequence", poll_period_s=0.002)
        engine = RunEngine({})
        writer = RunFileWriter(directory)
        engine.subscribe(writer)
        timer = None

        def abort_after_readout(event_name, doc, engine=engine):
            nonlocal timer
            if event_name == "event_page" and "voltage_v" in doc["data"] and timer is None:
                timer = threading.Timer(0.01, engine.abort)
                timer.start()

        if name == "aborted":
            engine.subscribe(abort_after_readout)
            try:
                with pytest.raises(RunEngineInterrupted):
                    engine(run_sequence(flyer, request, flush_period_s=0.02))
            finally:
                if timer is not None:
                    timer.join(2)
                    assert not timer.is_alive()
        else:
            engine(run_sequence(flyer, request))
        paths[name] = writer.path

    paths["incomplete"] = directory / "incomplete.h5"
    with RunFile.create(
        paths["incomplete"],
        measurement_id="in-progress",
        equipment={},
        sequence=Sequence((rest_step(10, Sampling(0.1)),)),
        limits=limits,
        start_mode="external",
        run_sheet_row={"protocol": "rest"},
    ) as writer:
        chunk = {key: np.zeros(3) for key in READOUT_FIELDS}
        chunk.update(t_s=np.array([0.1, 0.2, 0.3]), current_a=np.full(3, 0.001))
        writer.append_readout(chunk)
        writer.append_event(0.3, 0, 0, "error", "test error event")
    paths["unreadable"] = directory / "corrupt.h5"
    paths["unreadable"].write_bytes(b"not an HDF5 file\x00\xff")
    # Explicit ordering independent of filesystem timestamp granularity.
    for order, path in enumerate(paths.values(), start=1):
        os.utime(path, ns=(order * 10**9, order * 10**9))
    (directory / "notes.txt").write_text("ignore")
    nested = directory / "nested"
    nested.mkdir()
    (nested / "ignored.h5").write_bytes(b"ignore nested files")
    (directory / "directory.h5").mkdir()
    return directory, paths
