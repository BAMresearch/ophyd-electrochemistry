"""Folding an oe-archive-retained archive into a copy of a run file the host lost."""

import json

import h5py
import numpy as np
import pytest

from ophyd_electrochemistry.exceptions import ValidationError
from ophyd_electrochemistry.keithley.k2460.sequencer import (
    Replayer,
    SequencerProfile,
    compile_program,
)
from ophyd_electrochemistry.sequences.builders import (
    cc_step,
    cycling_sequence,
    pulse_train_sequence,
    rest_step,
)
from ophyd_electrochemistry.sequences.models import (
    CutoffAction,
    RepeatBlock,
    SafetyLimits,
    Sampling,
    Sequence,
    StepMode,
)
from ophyd_electrochemistry.sequences.recovery import (
    default_destination,
    main,
    recover_run_file,
)
from ophyd_electrochemistry.sequences.run_file import READOUT_FIELDS, RunFile, read_run_file
from ophyd_electrochemistry.sequences.simulator import CellModel, simulate_sequence

LIMITS = SafetyLimits(-2, 2.5, 0.005, 1e6, 999984, 0.001)
IDENTITY = "KEITHLEY INSTRUMENTS,MODEL 2460,04686198,1.7.16a"
FLAT = CellModel(ocv_soc=(0.0, 1.0), ocv_v=(1.0, 1.0), noise_v=1e-6)
EPOCH_S = 1794482155  # instrument clock at OE_T0, whole seconds
EPOCH_FRACTION = 0.25
CLOCK_OFFSET_S = 4181.1277  # instrument clock minus UTC, as measured by the backend


def sequence_for(case):
    if case == "pulse":
        return pulse_train_sequence(
            0.001, 0.1, 4, rest_s=0.6, pre_rest_s=0.6, fast_interval_s=0.006
        )
    if case == "cc_cv":  # voltage steps swap reading and source in the raw records
        return cycling_sequence(
            0.001,
            0.96,
            1.04,
            2,
            rest_s=1,
            cv_i_abs_min=0.0007,
            cv_max_s=30,
            max_step_s=30,
            interval_s=0.1,
        )
    return Sequence(  # cc_stop: the first cutoff stops the run
        (
            cc_step(0.001, 1.04, 30, Sampling(0.1), CutoffAction.STOP),
            rest_step(1, Sampling(0.1)),
            cc_step(-0.001, 0.96, 30, Sampling(0.1), CutoffAction.STOP),
        ),
        RepeatBlock(0, 2, 3),
    )


def make_run(
    tmp_path,
    case="pulse",
    cut=None,
    seed=0,
    outcome=None,
    equipment=None,
    meta=None,
    shift_s=0,
    origin=True,
):
    """A run file holding the first ``cut`` records (as the live backend wrote them)
    and the archive of every record (as archive_retained saves them)."""
    sequence = sequence_for(case)
    program = compile_program(sequence, LIMITS)
    sim = simulate_sequence(sequence, LIMITS, FLAT, seed=seed)
    r = sim.readout
    volt = np.array([s.mode == StepMode.VOLTAGE for s in sequence.steps])[r["step_index"]]
    reading = np.where(volt, r["current_a"], r["voltage_v"])
    source = np.where(volt, r["voltage_v"], r["current_a"])
    n = len(reading)
    cut = n // 3 if cut is None else cut
    columns = (r["t_s"], reading, source, r["measure_status"], r["source_status"])
    live, events = Replayer(program).feed(*(c[:cut] for c in columns))
    run_path = tmp_path / f"m-{case}_20261112T151249Z.h5"
    settings = {
        **program.settings(),
        "identity": IDENTITY,
        "runtime_build": "seq-v2",
        "clock_offset_s": CLOCK_OFFSET_S,
    }
    writer = RunFile.create(
        run_path,
        measurement_id=f"m-{case}",
        equipment={**settings, **(equipment or {})},
        sequence=sequence,
        limits=LIMITS,
        start_mode="immediate",
    )
    if origin:
        writer.set_time_origin(EPOCH_S + EPOCH_FRACTION - CLOCK_OFFSET_S)
    writer.append_readout(live)
    for event in events:
        writer.append_event(*event)
    if outcome:
        writer.finalize(outcome, "Status(... success=False)")
    writer.close()
    clock = r["t_s"] + EPOCH_FRACTION
    metadata = {
        "identity": IDENTITY,
        "runtime_build": "seq-v2",
        "status": ["idle", "trigger.STATE_IDLE", n, 0],
        "first_record": 1,
        "archived_utc_s": 0.0,
        **(meta or {}),
    }
    archive = tmp_path / "retained-20261113T094609Z.npz"
    np.savez(
        archive,
        seconds=EPOCH_S + shift_s + np.floor(clock).astype("i8"),
        fraction=clock % 1,
        reading=reading,
        source=source,
        status=r["measure_status"],
        source_status=r["source_status"],
        metadata=np.array(json.dumps(metadata)),
    )
    return run_path, archive, sim


@pytest.mark.parametrize(
    ("case", "prediction"),
    [("pulse", "completed"), ("cc_cv", "completed"), ("cc_stop", "stopped_cutoff")],
)
def test_recovery_restores_every_record_and_event(tmp_path, case, prediction):
    run_path, archive, sim = make_run(tmp_path, case)
    before = run_path.read_bytes(), archive.read_bytes()
    path, provenance = recover_run_file(run_path, archive)
    assert path == tmp_path / f"m-{case}-recovered_20261112T151249Z.h5"
    data = read_run_file(path)
    for name in READOUT_FIELDS:
        if name == "t_s":
            np.testing.assert_allclose(data["readout"][name], sim.readout[name], atol=1e-6, rtol=0)
        else:
            np.testing.assert_array_equal(data["readout"][name], sim.readout[name])
    events = list(
        zip(
            *(data["events"][k] for k in ("step_index", "repeat_index", "kind", "detail")),
            strict=True,
        )
    )
    assert events == [e[1:] for e in sim.events]
    assert sim.outcome == prediction == provenance["replay_prediction"]
    assert data["run"]["outcome"] == "incomplete"  # never reported as completed
    assert f"replay predicts {prediction}" in data["run"]["outcome_detail"]
    assert provenance["records_added"] == len(sim.readout["t_s"]) - provenance["records_in_file"]
    with h5py.File(path, "r") as file:  # plain HDF5 tools open it
        stored = json.loads(file["run/recovery"].asstr()[()])
    assert stored["archive_sha256"] == provenance["archive_sha256"]
    assert (run_path.read_bytes(), archive.read_bytes()) == before


def test_an_error_outcome_is_kept_with_its_detail(tmp_path):
    run_path, archive, _ = make_run(tmp_path, outcome="error")
    path, _ = recover_run_file(run_path, archive, tmp_path / "out.h5")
    run = read_run_file(path)["run"]
    assert run["outcome"] == "error"
    assert run["outcome_detail"].startswith("Status(... success=False); recovered:")


def test_a_file_already_complete_in_records_gains_nothing_but_provenance(tmp_path):
    run_path, archive, sim = make_run(tmp_path, cut=10**6)
    path, provenance = recover_run_file(run_path, archive)
    assert provenance["records_added"] == provenance["events_added"] == 0
    assert len(read_run_file(path)["readout"]["t_s"]) == len(sim.readout["t_s"])


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"outcome": "completed"}, "outcome 'completed'"),
        ({"cut": 0}, "no readings"),
        ({"equipment": {"capacity": 1}}, "recompiled program differs"),
        ({"equipment": {"identity": "another 2460"}}, "identity"),
        ({"meta": {"runtime_build": "seq-v1"}}, "runtime_build"),
        ({"meta": {"first_record": 2}}, "record 1"),
        ({"meta": {"status": ["idle", "trigger.STATE_IDLE", 10**6, 0]}}, "every record"),
        ({"origin": False}, "time origin"),
        # the same run a day later: equal readings, other instrument times (brief 15)
        ({"cut": 1, "shift_s": 86400}, "reproduce the file's t_s"),
    ],
)
def test_recovery_refuses_an_archive_that_is_not_the_runs(tmp_path, kwargs, message):
    run_path, archive, _ = make_run(tmp_path, **kwargs)
    with pytest.raises(ValidationError, match=message):
        recover_run_file(run_path, archive)
    assert not default_destination(run_path).exists()


def test_recovery_refuses_another_runs_archive(tmp_path):
    run_path, _, _ = make_run(tmp_path, seed=0)
    other = tmp_path / "other"
    other.mkdir()
    _, archive, _ = make_run(other, seed=1)  # same program, other noise
    with pytest.raises(ValidationError, match="does not reproduce"):
        recover_run_file(run_path, archive)
    assert not default_destination(run_path).exists()


def test_recovery_never_overwrites(tmp_path):
    run_path, archive, _ = make_run(tmp_path)
    for target in (run_path, archive):
        with pytest.raises(ValidationError, match="new output file"):
            recover_run_file(run_path, archive, target)
    taken = tmp_path / "taken.h5"
    taken.write_bytes(b"x")
    with pytest.raises(ValidationError, match="exists"):
        recover_run_file(run_path, archive, taken)
    assert taken.read_bytes() == b"x"


def test_profile_round_trips_through_the_recorded_settings():
    profile = SequencerProfile(
        reading_s={0.1: 0.005, 1: 0.041},
        marker_line=3,
        step_entry_s=0.002,
        start_on_lowest_range=False,
    )
    program = compile_program(sequence_for("pulse"), LIMITS, profile)
    assert SequencerProfile.from_settings(program.settings()) == profile
    assert SequencerProfile.from_settings({}) == SequencerProfile()


def test_cli_writes_beside_the_run(tmp_path, capsys):
    run_path, archive, _ = make_run(tmp_path)
    assert main([str(run_path), str(archive)]) == 0
    assert "replay predicts completed; outcome kept (incomplete)" in capsys.readouterr().out
    assert default_destination(run_path).exists()


def test_a_field_left_longer_by_a_killed_writer_is_cut_back(tmp_path):
    run_path, archive, sim = make_run(tmp_path)
    with h5py.File(run_path, "r+", libver=("v110", "v110")) as file:  # one extra timestamp
        times = file["readout/t_s"]
        times.resize((len(times) + 1,))
        times[-1] = times[-2] + 0.006
    path, _ = recover_run_file(run_path, archive)
    data = read_run_file(path)["readout"]
    assert {len(v) for v in data.values()} == {len(sim.readout["t_s"])}
    np.testing.assert_allclose(data["t_s"], sim.readout["t_s"], atol=1e-6, rtol=0)


@pytest.mark.parametrize("stage", ["copy", "link"])
def test_a_failure_leaves_no_file_behind(tmp_path, monkeypatch, stage):
    import ophyd_electrochemistry.sequences.recovery as recovery

    run_path, archive, _ = make_run(tmp_path)
    before = sorted(p.name for p in tmp_path.iterdir())
    if stage == "copy":
        real = recovery.repair_run_file

        def broken(source, destination):
            real(source, destination)
            raise OSError("disk full")

        monkeypatch.setattr(recovery, "repair_run_file", broken)
    else:
        monkeypatch.setattr(recovery.os, "link", lambda *a: (_ for _ in ()).throw(OSError("x")))
    with pytest.raises(OSError):
        recover_run_file(run_path, archive)
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_cli_reports_a_refusal_in_one_line(tmp_path, capsys):
    run_path, archive, _ = make_run(tmp_path, meta={"first_record": 2})
    assert main([str(run_path), str(archive)]) == 1
    assert "refused, nothing written: the archive does not start" in capsys.readouterr().out
