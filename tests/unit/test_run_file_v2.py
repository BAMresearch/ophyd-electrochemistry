"""The NeX-ish v2 run-file layout: groups, units, descriptions, metadata sources."""

import hashlib
import json
import math
from pathlib import Path

import h5py
import pytest
from bluesky import RunEngine

from ophyd_electrochemistry.exceptions import ValidationError
from ophyd_electrochemistry.keithley.k2460.sequencer import compile_program
from ophyd_electrochemistry.sequences import (
    SafetyLimits,
    SequenceFlyer,
    SequenceRequest,
    SimulatedSequenceBackend,
    bipolar_pulse_sequence,
    run_sequence,
)
from ophyd_electrochemistry.sequences import run_file as run_file_module
from ophyd_electrochemistry.sequences.builders import cycling_sequence, pulse_train_sequence
from ophyd_electrochemistry.sequences.callbacks import RunFileWriter
from ophyd_electrochemistry.sequences.queueserver import (
    build_namespace,
    load_session,
    sheet_digest,
)
from ophyd_electrochemistry.sequences.run_file import (
    SCHEMA,
    SCHEMA_V1,
    RunFile,
    read_run_file,
    repair_run_file,
)
from ophyd_electrochemistry.sequences.run_file_fields import instrument_place
from ophyd_electrochemistry.sequences.run_sheet import make_template

LIMITS = SafetyLimits(0, 3, 0.005, 1e6, 999984)
V1_FIXTURE = Path(__file__).parents[1] / "fixtures" / "run-file-v1.h5"
SESSION_TEXT = """# Test cell: 1.78 mg hard carbon vs Na, front terminals, four-wire.
# Loading this session confirms set-up and limits.

backend = "simulator"
data_dir = "{data}"
sim_speed = inf

[limits]
v_min_abs = 0.0
v_max_abs = 3.0
i_max_abs = 0.01
max_duration_s = 200000.0
max_records = 1000000

[sample]
cell = "AlCe Test Cell 1"
hc_mass_mg = 1.78
capacity_mah = 0.26
electrolyte = "1 M NaPF6 in diglyme"
vented = false
"""


def programs():
    pulse = pulse_train_sequence(0.001, 0.1, 4, rest_s=0.6, pre_rest_s=0.6, fast_interval_s=0.006)
    cycle = cycling_sequence(0.001, 0.5, 2.5, 1, cv_i_abs_min=1e-4, cv_max_s=60, max_step_s=60)
    return [
        compile_program(pulse, LIMITS),
        compile_program(cycle, LIMITS),
        compile_program(pulse, LIMITS, external_start=True),
    ]


def create(path, equipment=None, **kwargs):
    return RunFile.create(
        path,
        measurement_id="m-1",
        equipment=programs()[1].settings() if equipment is None else equipment,
        sequence=bipolar_pulse_sequence(0.001, 0.1, 2),
        limits=LIMITS,
        start_mode="immediate",
        **kwargs,
    )


def datasets(file):
    found = []
    file.visititems(
        lambda name, item: found.append((name, item)) if isinstance(item, h5py.Dataset) else None
    )
    return found


@pytest.mark.parametrize("index", [0, 1, 2])
def test_every_program_setting_has_a_described_place(index):
    settings = programs()[index].settings()
    for key in settings:
        group, _, _, description = instrument_place(key, settings)
        assert group != "other", key
        assert description, key


def test_simulator_settings_have_a_described_place():
    equipment = SimulatedSequenceBackend().equipment()
    assert {instrument_place(key, equipment)[0] for key in equipment} <= {"identity", "simulator"}


def test_layout_classes_units_and_descriptions(tmp_path):
    path = tmp_path / "m.h5"
    with create(path) as writer:
        writer.finalize("completed")
    with h5py.File(path, "r") as file:
        assert file.attrs["schema"] == SCHEMA
        assert file.attrs["default"] == "entry"
        assert file["entry"].attrs["NX_class"] == "NXentry"
        assert file["entry"].attrs["default"] == "readout"
        assert file["entry/sample"].attrs["NX_class"] == "NXsample"
        assert file["entry/instrument"].attrs["NX_class"] == "NXinstrument"
        readout = file["entry/readout"]
        assert readout.attrs["NX_class"] == "NXdata"
        assert readout.attrs["signal"] == "voltage_v"
        assert list(readout.attrs["axes"]) == ["t_s"]
        for name, item in datasets(file):
            assert item.attrs.get("description"), name
        instrument = file["entry/instrument"]
        assert set(instrument) >= {"runtime", "limits", "timing", "program", "source_1"}
        assert "other" not in instrument
        # A voltage-source entry has its level in V and its limit in A.
        modes = {
            g: instrument[g]["mode"].asstr()[()] for g in instrument if g.startswith("source_")
        }
        assert set(modes.values()) == {"current", "voltage"}
        for group, mode in modes.items():
            level, limit = ("A", "V") if mode == "current" else ("V", "A")
            assert instrument[group]["level"].attrs["units"] == level
            assert instrument[group]["range"].attrs["units"] == level
            assert instrument[group]["limit"].attrs["units"] == limit
        assert instrument["limits/v_max_abs"].attrs["units"] == "V"
        assert instrument["timing/reading_s_nplc_0p1"].attrs["units"] == "s"
        assert "units" not in instrument["digital_io/start_line"].attrs
        assert readout["t_s"].attrs["units"] == "s"
        assert math.isnan(file["entry/run/time_origin_epoch_s"][()])
        assert file["entry/run/expected_records"][()] > 0


def test_equipment_round_trips_exactly_including_unknown_keys(tmp_path):
    equipment = {**programs()[0].settings(), "new_setting": 3, "pre_run_instrument_events": "x"}
    path = tmp_path / "m.h5"
    create(path, equipment).close()
    assert read_run_file(path)["equipment"] == equipment
    with h5py.File(path, "r") as file:
        assert file["entry/instrument/other/new_setting"][()] == 3
        assert file["entry/instrument/eventlog/pre_run_instrument_events"].asstr()[()] == "x"


def test_metadata_is_validated_before_the_file_exists(tmp_path):
    path = tmp_path / "m.h5"
    for kwargs in (
        {"sample": {"name": "x"}},  # collides with the run sheet's sample ID
        {"sample": {"layers": [1, 2]}},
        {"provenance": {"bad": math.nan}},
        {"provenance": {"a/b": "x"}},
        {"equipment": {"a/b": 1}},
    ):
        with pytest.raises(ValidationError):
            create(path, **kwargs)
        assert not path.exists()


def test_none_leaves_a_value_out_and_mappings_become_json(tmp_path):
    path = tmp_path / "m.h5"
    session = {"format": "notebook", "path": None, "text": None, "settings": {"serial": None}}
    create(path, session=session, provenance={"scan_id": None, "host": "h"}).close()
    data = read_run_file(path)
    assert data["session"] == {"format": "notebook", "settings": {"serial": None}}
    assert data["provenance"] == {"host": "h"}
    assert "session_description" not in data["sample"]


def test_an_external_start_aborted_before_any_reading_has_no_time_origin(tmp_path):
    path = tmp_path / "m.h5"
    with create(path) as writer:
        writer.finalize("aborted", "no START within 10 s")
    run = read_run_file(path)["run"]
    assert run["outcome"] == "aborted"
    assert "time_origin_epoch_s" not in run and "time_origin_utc" not in run


def test_time_origin_is_set_once(tmp_path):
    with create(tmp_path / "m.h5") as writer:
        writer.set_time_origin(1794482155.25)
        with pytest.raises(RuntimeError, match="already set"):
            writer.set_time_origin(1794482156.0)


def test_files_written_before_v2_still_read_and_repair_as_v1(tmp_path):
    data = read_run_file(V1_FIXTURE)
    assert data["attrs"]["schema"] == SCHEMA_V1
    assert data["attrs"]["measurement_id"] == "v1-fixture"
    assert data["run"]["outcome"] == "completed"
    assert data["run"]["sample_id"] == "dummy"
    assert data["run"]["time_origin_epoch_s"] == 1794482155.25 - 4181.1277
    assert data["equipment"]["identity"].startswith("KEITHLEY")
    assert len(data["readout"]["t_s"]) == 5
    assert list(data["events"]["kind"]) == ["step_start"]
    assert data["sample"] == data["session"] == data["provenance"] == {}
    copy = tmp_path / "copy.h5"
    repair_run_file(V1_FIXTURE, copy)
    assert repr(read_run_file(copy)) == repr(data)


def write_session(tmp_path, text=SESSION_TEXT):
    path = tmp_path / "session.toml"
    path.write_text(text.format(data=tmp_path / "data"), encoding="utf-8")
    return path


def test_a_queued_run_stores_session_sample_and_provenance(tmp_path):
    session_path = write_session(tmp_path)
    session = load_session(session_path)
    sheet = make_template(tmp_path / "runs.xlsx")
    namespace = build_namespace(session, backend=SimulatedSequenceBackend(speed=math.inf))
    uids = []
    namespace["RE"].subscribe(
        lambda name, doc: uids.append(doc["uid"]) if name == "start" else None
    )
    digest = sheet_digest(sheet)
    namespace["RE"](namespace["run_from_sheet"](str(sheet), "example-rest", digest))
    data = read_run_file(namespace["run_file_writer"].path)

    raw = session_path.read_bytes()
    assert data["session"]["text"] == raw.decode("utf-8")
    assert data["session"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert data["session"]["path"] == str(session_path.resolve())
    assert data["session"]["format"] == "toml"
    assert data["session"]["settings"]["limits"]["v_max_abs"] == 3.0
    assert data["session"]["settings"]["sim_speed"] == "inf"
    assert data["sample"]["session_description"].startswith("Test cell: 1.78 mg hard carbon")
    assert data["sample"]["hc_mass_mg"] == 1.78
    assert data["sample"]["vented"] is False
    provenance = data["provenance"]
    assert provenance["bluesky_uid"] == uids[0]
    assert provenance["scan_id"] == 1
    assert provenance["plan_name"] == "run_sequence"
    assert provenance["run_sheet_path"] == str(sheet)
    assert provenance["run_sheet_sha256"] == digest
    assert provenance["host"]
    assert {"python", "ophyd-electrochemistry", "bluesky", "h5py"} <= set(provenance["software"])
    with h5py.File(namespace["run_file_writer"].path, "r") as file:
        sample = file["entry/sample"]
        assert sample["hc_mass_mg"].attrs["units"] == "mg"
        assert sample["capacity_mah"].attrs["units"] == "mA h"
        assert "units" not in sample["cell"].attrs


@pytest.mark.parametrize(
    ("table", "message"),
    [
        ("[sample.anode]\nmass_mg = 1\n", "only text, numbers"),
        ("[sample]\nlayers = [1, 2]\n", "only text, numbers"),
        ("[sample]\nmass_mg = nan\n", "finite"),
        ("[sample]\nmade = 2026-11-01\n", "only text, numbers"),
        ('[sample]\nname = "x"\n', "reserved"),
        ('[sample]\nsession_description = "x"\n', "reserved"),
        ("[sample]\nMass = 1\n", "lower-case"),
    ],
)
def test_the_sample_table_is_descriptive_and_flat(tmp_path, table, message):
    text = SESSION_TEXT.split("[sample]")[0] + table
    with pytest.raises(ValidationError, match=message):
        load_session(write_session(tmp_path, text))


def test_the_sample_table_changes_nothing_a_run_uses(tmp_path):
    with_sample = load_session(write_session(tmp_path))
    without = load_session(write_session(tmp_path, SESSION_TEXT.split("[sample]")[0]))
    assert with_sample.limits == without.limits
    assert (with_sample.backend, with_sample.data_dir) == (without.backend, without.data_dir)
    assert with_sample.sample["cell"] == "AlCe Test Cell 1" and without.sample == {}


def test_custom_runstart_metadata_goes_to_extra(tmp_path):
    request = SequenceRequest("m-1", bipolar_pulse_sequence(0.001, 0.1, 2), LIMITS)
    flyer = SequenceFlyer(SimulatedSequenceBackend(speed=math.inf), name="k", poll_period_s=0.002)
    engine = RunEngine({"beamline": "ID02"})
    writer = RunFileWriter(tmp_path)
    engine.subscribe(writer)
    engine(run_sequence(flyer, request, md={"saxs_scan": {"file": "s.h5", "frames": 3}}))
    data = read_run_file(writer.path)
    assert data["extra"]["beamline"] == "ID02"
    assert data["extra"]["saxs_scan"] == {"file": "s.h5", "frames": 3}
    assert data["provenance"]["scan_id"] == 1
    assert "run_sheet_path" not in data["provenance"]  # not run from a sheet


def test_absent_optional_packages_are_left_out(monkeypatch):
    real = run_file_module.importlib_metadata.version

    def version(name):
        if name == "pyvisa":
            raise run_file_module.importlib_metadata.PackageNotFoundError(name)
        return real(name)

    monkeypatch.setattr(run_file_module.importlib_metadata, "version", version)
    software = run_file_module.host_provenance()["software"]
    assert "pyvisa" not in software and "numpy" in software
    json.dumps(software)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"equipment": {".": 1}},
        {"equipment": {"..": 1}},
        {"provenance": {"software": "unknown"}},
        {"session": {"settings": "unknown"}},
        {"session": {"settings": ["a"]}},
    ],
)
def test_names_and_json_objects_are_checked_before_the_file_exists(tmp_path, kwargs):
    path = tmp_path / "m.h5"
    with pytest.raises(ValidationError):
        create(path, **kwargs)
    assert not path.exists()


def test_a_write_that_fails_after_creation_leaves_no_file(tmp_path, monkeypatch):
    def broken(*args):
        raise OSError("disk full")

    monkeypatch.setattr(run_file_module, "_write_v2", broken)
    path = tmp_path / "m.h5"
    with pytest.raises(OSError, match="disk full"):
        create(path)
    assert not path.exists()
