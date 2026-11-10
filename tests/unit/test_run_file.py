"""Incremental run-file persistence, metadata and lifecycle contracts."""

import json
import subprocess
import sys
from datetime import datetime
from types import MappingProxyType

import h5py
import numpy as np
import pytest

from ophyd_electrochemistry import __version__
from ophyd_electrochemistry.exceptions import ValidationError
from ophyd_electrochemistry.sequences import (
    SafetyLimits,
    bipolar_pulse_sequence,
    limits_from_dict,
    sequence_from_dict,
    to_dict,
)
from ophyd_electrochemistry.sequences.run_file import (
    READOUT_FIELDS,
    RunFile,
    read_run_file,
    repair_run_file,
)


@pytest.fixture
def settings():
    return dict(
        measurement_id="pulse-001",
        equipment=MappingProxyType(
            dict(
                model="2460", serial="01234567", offmode="HIGH-Z", nplc=0.1, sense=4, simulated=True
            )
        ),
        sequence=bipolar_pulse_sequence(0.001, 0.1, 2),
        limits=SafetyLimits(0, 4, 0.01, 1e6, 10**7),
        start_mode="immediate",
    )


def chunk(offset=0, count=3):
    return {
        "t_s": np.arange(offset, offset + count, dtype=float) * 0.01,
        "voltage_v": np.linspace(2.1, 2.2, count),
        "current_a": [-0.001] * count,
        "step_index": [2] * count,
        "repeat_index": [1] * count,
        "source_status": [0] * count,
        "measure_status": [4] * count,
    }


def assert_utc(value):
    assert value.endswith("Z")
    assert datetime.fromisoformat(value).utcoffset().total_seconds() == 0


def test_round_trip_several_chunks_metadata_and_events(tmp_path, settings):
    path = tmp_path / "pulse.h5"
    row = {"measurement_id": "pulse-001", "operator": "Béa", "blank": None, "mass_mg": 2.5}
    extra = {"beamline": {"enabled": True, "positions": [1, 2]}, "optional": None}
    chunks = [chunk(0), chunk(3, 1), chunk(4, 2500)]
    with RunFile.create(
        path, **settings, sample_id="Na/硬碳", notes="test ΔV", run_sheet_row=row, extra=extra
    ) as writer:
        writer.append_event(0, 0, 0, "step_start")
        for values in chunks:
            writer.append_readout(values)
        writer.append_event(25.04, 2, 1, "cutoff", "voltage ≥ 4 V")
        writer.finalize("stopped_cutoff", "upper voltage limit")

    result = read_run_file(path)
    assert result["attrs"]["schema"] == "ophyd-electrochemistry.run-file.v1"
    assert result["attrs"]["measurement_id"] == "pulse-001"
    assert result["attrs"]["package_version"] == __version__
    assert_utc(result["attrs"]["created_utc"])
    assert result["equipment"] == settings["equipment"]
    assert type(result["equipment"]["simulated"]) is bool
    assert result["run"] == dict(
        start_mode="immediate",
        sample_id="Na/硬碳",
        notes="test ΔV",
        started_utc=result["run"]["started_utc"],
        finished_utc=result["run"]["finished_utc"],
        outcome="stopped_cutoff",
        outcome_detail="upper voltage limit",
    )
    assert_utc(result["run"]["started_utc"])
    assert_utc(result["run"]["finished_utc"])
    assert result["run"]["finished_utc"] >= result["run"]["started_utc"]
    assert sequence_from_dict(result["sequence"]) == settings["sequence"]
    assert limits_from_dict(result["safety_limits"]) == settings["limits"]
    assert result["run_sheet_row"] == row
    assert result["extra"] == extra
    assert tuple(result["readout"]) == READOUT_FIELDS
    for name in READOUT_FIELDS:
        np.testing.assert_array_equal(
            result["readout"][name], np.concatenate([values[name] for values in chunks])
        )
    for name, expected in dict(
        t_s=[0, 25.04],
        step_index=[0, 2],
        repeat_index=[0, 1],
        kind=["step_start", "cutoff"],
        detail=["", "voltage ≥ 4 V"],
    ).items():
        np.testing.assert_array_equal(result["events"][name], expected)

    with h5py.File(path, "r") as file:
        assert set(file) == {"equipment", "run", "readout", "events"}
        for index, (name, units) in enumerate(
            zip(READOUT_FIELDS, ("s", "V", "A", "", "", "", ""), strict=True)
        ):
            dataset = file["readout"][name]
            assert dataset.ndim == 1
            assert dataset.maxshape == (None,)
            assert dataset.chunks is not None
            assert dataset.compression == "gzip"
            assert dataset.dtype == np.dtype("f8" if index < 3 else "i4")
            assert dataset.attrs["units"] == units
        for name, metadata in dict(
            sequence=to_dict(settings["sequence"]),
            safety_limits=to_dict(settings["limits"]),
            run_sheet_row=row,
            extra=extra,
        ).items():
            assert file["run"][name].shape == ()
            assert file["run"][name].asstr()[()] == json.dumps(metadata, sort_keys=True)


def test_refuse_existing_path_without_changing_content(tmp_path, settings):
    path = tmp_path / "existing.h5"
    path.write_bytes(b"retained data")
    with pytest.raises(FileExistsError):
        RunFile.create(path, **settings)
    assert path.read_bytes() == b"retained data"


def test_read_flushed_snapshot_while_writer_is_open(tmp_path, settings):
    path = tmp_path / "live.h5"
    with RunFile.create(path, **settings) as writer:
        for offset in (0, 3, 6):
            writer.append_readout(chunk(offset))
            writer.append_event(offset * 0.01, 0, 0, "step_start")
            snapshot = read_run_file(path)
            assert len(snapshot["readout"]["t_s"]) == offset + 3
            assert len(snapshot["events"]["kind"]) == offset // 3 + 1
            assert snapshot["run"]["outcome"] == "incomplete"
        writer.finalize("completed")
        assert read_run_file(path)["run"]["outcome"] == "completed"
    # Returned arrays do not hold an HDF5 handle or change with future writes.
    assert len(snapshot["readout"]["t_s"]) == 9


def test_snapshot_mid_append_returns_whole_records_only(tmp_path, settings):
    # A live writer extends the fields one after another; a SWMR reader racing
    # it saw unequal lengths in 432 of 458 snapshots (3 Nov). Simulate that state.
    path = tmp_path / "mid-append.h5"
    with RunFile.create(path, **settings) as writer:
        writer.append_readout(chunk(0, 3))
        writer.append_event(0.0, 0, 0, "step_start")
        writer.finalize("completed")
    with h5py.File(path, "r+") as file:
        file["readout"]["voltage_v"].resize((5,))
        file["events"]["kind"].resize((2,))
    snapshot = read_run_file(path)
    assert {len(values) for values in snapshot["readout"].values()} == {3}
    assert {len(values) for values in snapshot["events"].values()} == {1}


def test_killed_writer_leaves_flushed_data_readable(tmp_path):
    # A crashed Jupyter kernel leaves the SWMR file flagged as open for writing.
    path = tmp_path / "killed.h5"
    script = f"""
import os
import numpy as np
from ophyd_electrochemistry.sequences import SafetyLimits, bipolar_pulse_sequence
from ophyd_electrochemistry.sequences.run_file import READOUT_FIELDS, RunFile
writer = RunFile.create(
    {str(path)!r}, measurement_id="m", equipment={{}},
    sequence=bipolar_pulse_sequence(1e-4, 0.1, 2),
    limits=SafetyLimits(0, 3, 0.01, 1e6, 10**7), start_mode="immediate",
)
for _ in range(3):
    writer.append_readout({{name: np.arange(500) for name in READOUT_FIELDS}})
writer.append_event(0.0, 0, 0, "step_start")
os._exit(0)
"""
    subprocess.run([sys.executable, "-c", script], check=True, timeout=60)
    result = read_run_file(path)
    assert len(result["readout"]["t_s"]) == 1500
    assert list(result["events"]["kind"]) == ["step_start"]
    assert result["run"]["outcome"] == "incomplete"
    # Plain HDF5 readers refuse the flagged file; the repaired copy opens anywhere.
    with pytest.raises(OSError, match="open for write"):
        h5py.File(path, "r")
    repaired = tmp_path / "repaired.h5"
    repair_run_file(path, repaired)
    with h5py.File(repaired, "r") as file:
        assert len(file["readout"]["t_s"]) == 1500
    copy = read_run_file(repaired)
    for name in ("attrs", "equipment", "run", "sequence", "safety_limits", "events"):
        assert repr(copy[name]) == repr(result[name])
    for name in READOUT_FIELDS:
        np.testing.assert_array_equal(copy["readout"][name], result["readout"][name])
    with pytest.raises(FileExistsError):
        repair_run_file(path, repaired)


def test_other_process_reads_while_writer_appends(tmp_path, settings):
    # The QueueServer case: a worker writes, a notebook process plots.
    path = tmp_path / "live.h5"
    script = f"""
import sys
import numpy as np
from ophyd_electrochemistry.sequences import SafetyLimits, bipolar_pulse_sequence
from ophyd_electrochemistry.sequences.run_file import READOUT_FIELDS, RunFile
with RunFile.create(
    {str(path)!r}, measurement_id="m", equipment={{}},
    sequence=bipolar_pulse_sequence(1e-4, 0.1, 2),
    limits=SafetyLimits(0, 3, 0.01, 1e6, 10**7), start_mode="immediate",
) as writer:
    for index in range(4):
        writer.append_readout({{name: np.arange(100) for name in READOUT_FIELDS}})
        writer.append_event(float(index), 0, index, "step_start", "é" * index)
        print(index, flush=True)
        sys.stdin.readline()
    writer.finalize("completed")
"""
    writer = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        for index in range(4):
            assert writer.stdout.readline().strip() == str(index)
            snapshot = read_run_file(path)
            assert len(snapshot["readout"]["t_s"]) == 100 * (index + 1)
            assert list(snapshot["events"]["detail"]) == ["é" * i for i in range(index + 1)]
            assert snapshot["run"]["outcome"] == "incomplete"
            writer.stdin.write("\n")
            writer.stdin.flush()
        assert writer.wait(timeout=60) == 0
    finally:
        writer.kill()
    assert read_run_file(path)["run"]["outcome"] == "completed"
    with h5py.File(path, "r") as file:  # Closed normally: plain readers work.
        assert len(file["readout"]["t_s"]) == 400


@pytest.mark.parametrize("exception", [False, True])
def test_close_without_finalize_keeps_partial_data_incomplete(tmp_path, settings, exception):
    path = tmp_path / "incomplete.h5"
    try:
        with RunFile.create(path, **settings) as writer:
            writer.append_readout(chunk())
            if exception:
                raise RuntimeError("acquisition failed")
    except RuntimeError as exc:
        assert str(exc) == "acquisition failed"
    writer.close()  # Idempotent.
    result = read_run_file(path)
    assert result["run"]["outcome"] == "incomplete"
    assert result["run"]["finished_utc"] == ""
    assert result["run"]["sample_id"] == ""
    assert result["run"]["notes"] == ""
    assert result["run_sheet_row"] == result["extra"] == {}
    assert len(result["readout"]["t_s"]) == 3


@pytest.mark.parametrize("outcome", ["completed", "stopped_cutoff", "aborted", "error"])
def test_finalize_is_terminal_and_persists_each_outcome(tmp_path, settings, outcome):
    path = tmp_path / "terminal.h5"
    with RunFile.create(path, **settings) as writer:
        writer.finalize(outcome, "detail")
        for action in (
            lambda: writer.append_readout(chunk()),
            lambda: writer.append_event(0, 0, 0, "error"),
            lambda: writer.finalize("completed"),
        ):
            with pytest.raises(RuntimeError, match="finalized"):
                action()
    assert read_run_file(path)["run"]["outcome"] == outcome
    assert read_run_file(path)["run"]["outcome_detail"] == "detail"


def test_closed_writer_rejects_mutation(tmp_path, settings):
    writer = RunFile.create(tmp_path / "closed.h5", **settings)
    writer.close()
    for action in (
        lambda: writer.append_readout(chunk()),
        lambda: writer.append_event(0, 0, 0, "error"),
        lambda: writer.finalize("completed"),
    ):
        with pytest.raises(RuntimeError, match="closed"):
            action()


@pytest.mark.parametrize(
    "change",
    [
        {"measure_status": [0]},  # Unequal length, including the last field.
        {"measure_status": [[0, 0, 0]]},
        {"measure_status": [0, 0, 0.5]},
        {"measure_status": [0, 0, 2**31]},
        {"measure_status": [0, 0, -(2**31) - 1]},
        {"measure_status": [0, 0, np.nan]},
        {"measure_status": [0, 0, np.inf]},
        {"current_a": ["0.001"] * 3},
        {"current_a": [1j] * 3},
        {"voltage_v": 2.1},
    ],
)
def test_bad_chunk_does_not_extend_any_dataset(tmp_path, settings, change):
    path = tmp_path / "invalid-chunk.h5"
    with RunFile.create(path, **settings) as writer:
        writer.append_readout(chunk())
        with pytest.raises(ValidationError):
            writer.append_readout(chunk() | change)
        snapshot = read_run_file(path)
        assert {len(value) for value in snapshot["readout"].values()} == {3}
        writer.append_readout(chunk(3))
    assert len(read_run_file(path)["readout"]["t_s"]) == 6


@pytest.mark.parametrize("missing", [True, False])
def test_readout_requires_exact_fields(tmp_path, settings, missing):
    with RunFile.create(tmp_path / "fields.h5", **settings) as writer:
        values = chunk()
        if missing:
            del values["measure_status"]
        else:
            values["typo"] = [0, 0, 0]
        with pytest.raises(ValidationError, match="exactly"):
            writer.append_readout(values)


def test_empty_run_and_empty_chunk(tmp_path, settings):
    path = tmp_path / "empty.h5"
    with RunFile.create(path, **settings) as writer:
        writer.append_readout({name: [] for name in READOUT_FIELDS})
        writer.finalize("completed")
    result = read_run_file(path)
    assert all(value.size == 0 for value in result["readout"].values())
    assert all(value.size == 0 for value in result["events"].values())


@pytest.mark.parametrize(
    "value", [None, [], {}, object(), float("nan"), float("inf"), b"bytes", "nul\x00", 2**100]
)
def test_equipment_rejects_unsupported_values_before_creating_file(tmp_path, settings, value):
    path = tmp_path / "bad-equipment.h5"
    with pytest.raises(ValidationError, match="equipment"):
        RunFile.create(path, **(settings | {"equipment": {"model": value}}))
    assert not path.exists()


@pytest.mark.parametrize("name", ["extra", "run_sheet_row"])
@pytest.mark.parametrize("value", [object(), float("nan"), float("inf")])
def test_metadata_rejects_non_json_values_before_creating_file(tmp_path, settings, name, value):
    path = tmp_path / "bad-json.h5"
    with pytest.raises(ValidationError, match=name):
        RunFile.create(path, **settings, **{name: {"nested": [value]}})
    assert not path.exists()


def test_bad_event_or_outcome_does_not_prevent_valid_writes(tmp_path, settings):
    path = tmp_path / "bad-event.h5"
    with RunFile.create(path, **settings) as writer:
        with pytest.raises(ValidationError, match="kind"):
            writer.append_event(0, 0, 0, "typo")
        with pytest.raises(ValidationError, match="detail"):
            writer.append_event(0, 0, 0, "error", "bad\x00text")
        with pytest.raises(ValidationError, match="step_index"):
            writer.append_event(0, 2**31, 0, "error")
        with pytest.raises(ValidationError, match="outcome"):
            writer.finalize("success")
        writer.append_event(0, 0, 0, "error", "δ" * 10_000)
        writer.finalize("error")
    result = read_run_file(path)
    assert result["events"]["detail"].tolist() == ["δ" * 10_000]
    assert result["run"]["outcome"] == "error"


def test_reader_rejects_other_schema(tmp_path):
    path = tmp_path / "other.h5"
    with h5py.File(path, "x") as file:
        file.attrs["schema"] = "different"
    with pytest.raises(ValidationError, match="schema"):
        read_run_file(path)


def test_time_origin_is_flushed_and_can_only_be_set_once(tmp_path, settings):
    path = tmp_path / "origin.h5"
    with RunFile.create(path, **settings) as writer:
        writer.set_time_origin(1700000000.125)
        run = read_run_file(path)["run"]
        assert run["time_origin_epoch_s"] == 1700000000.125
        assert run["time_origin_utc"] == "2023-11-14T22:13:20.125000Z"
        with pytest.raises(RuntimeError, match="already set"):
            writer.set_time_origin(1700000001)
        writer.finalize("completed")
        with pytest.raises(RuntimeError, match="finalized"):
            writer.set_time_origin(1700000001)
    with pytest.raises(RuntimeError, match="closed"):
        writer.set_time_origin(1700000001)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "123"])
def test_invalid_time_origin_does_not_set_attrs(tmp_path, settings, value):
    path = tmp_path / "origin-invalid.h5"
    with RunFile.create(path, **settings) as writer:
        with pytest.raises(ValidationError):
            writer.set_time_origin(value)
        assert "time_origin_epoch_s" not in read_run_file(path)["run"]
        writer.set_time_origin(0)
