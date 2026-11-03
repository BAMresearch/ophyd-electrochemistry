"""Run sheets are validated offline, preserving the original row as metadata."""

import json
from dataclasses import FrozenInstanceError, replace
from datetime import datetime

import pandas as pd
import pytest
from openpyxl import load_workbook

from ophyd_electrochemistry.exceptions import ValidationError
from ophyd_electrochemistry.sequences import (
    RunSpec,
    SafetyLimits,
    Sampling,
    bipolar_pulse_sequence,
    cycling_sequence,
    load_run_sheet,
    log_repeats,
    make_template,
    renard_series,
    soc_position_sequence,
    validate,
)


@pytest.fixture
def limits():
    return SafetyLimits(0, 3, 0.01, 1e6, 10**7)


def write_sheet(tmp_path, rows, *, columns=None):
    path = tmp_path / "runs.xlsx"
    pd.DataFrame(rows, columns=columns).to_excel(path, index=False, sheet_name="Runs")
    return path


def pulse(**changes):
    return (
        dict(measurement_id="pulse", protocol="bipolar_pulse", current_a=1e-4, pulse_s=0.1)
        | changes
    )


def test_template_round_trip(tmp_path, limits):
    path = make_template(tmp_path / "template.xlsx")
    runs = load_run_sheet(path, limits)
    assert len(runs) == 20  # Four protocol examples plus the full 16-run R5 series.
    series = [run for name, run in runs.items() if name.startswith("soc01-")]
    assert [run.row["pulse_s"] for run in series] == renard_series(0.1, 100)
    assert all(run.row["current_a"] == 1e-4 for run in series)
    assert all(run.sequence.repeat.count == log_repeats(run.row["pulse_s"]) for run in series)
    assert {run.row["protocol"] for run in runs.values()} == {
        "bipolar_pulse",
        "cycling",
        "soc_position",
        "rest",
    }
    for run in runs.values():
        validate(run.sequence, limits)
        json.dumps(run.row, allow_nan=False)
        assert "PLACEHOLDER" in run.notes
    assert runs["example-pulse"].row_number == 3  # Row 2 describes the columns.
    assert load_run_sheet(path, limits, sheet_name="Runs") == runs
    workbook = load_workbook(path)
    assert workbook.sheetnames == ["Runs", "README"]
    headers = [cell.value for cell in workbook["Runs"][1]]
    assert headers[0] == "measurement_id"
    descriptions = dict(zip(headers, (cell.value for cell in workbook["Runs"][2]), strict=True))
    assert descriptions["measurement_id"].startswith("# ")
    assert all(descriptions.values())
    assert "Not the absolute safety limit" in descriptions["v_high"]
    readme = dict(workbook["README"].values)
    assert set(headers) <= readme.keys()
    notes = {cell.value: cell.comment.text for cell in workbook["Runs"][1]}
    assert notes == descriptions | {"measurement_id": descriptions["measurement_id"][2:]}
    assert workbook["Runs"].freeze_panes == "B2"
    assert workbook["Runs"]["A3"].number_format == "@"
    workbook.close()


def test_comment_rows_are_skipped(tmp_path, limits):
    rows = [
        dict(measurement_id="# what each column means", current_a="amplitude in A"),
        pulse(measurement_id="#pulse"),  # A run switched off; not a duplicate of "pulse".
        pulse(),
    ]
    runs = load_run_sheet(write_sheet(tmp_path, rows), limits)
    assert list(runs) == ["pulse"]
    assert runs["pulse"].row_number == 4


def test_template_refuses_overwrite(tmp_path):
    path = tmp_path / "template.xlsx"
    path.write_bytes(b"existing contents")
    with pytest.raises(FileExistsError):
        make_template(path)
    assert path.read_bytes() == b"existing contents"
    assert make_template(path, overwrite=True) == path
    workbook = load_workbook(path)
    assert workbook.sheetnames == ["Runs", "README"]
    workbook.close()
    with pytest.raises(ValueError, match="xlsx"):
        make_template(tmp_path / "template.csv")


def test_pulse_defaults_and_raw_metadata(tmp_path, limits):
    path = write_sheet(
        tmp_path,
        [
            pulse(
                start_mode=None,
                repeats=None,
                rest_s=None,
                pre_rest_s=None,
                fast_interval_s=None,
                fast_window_s=None,
                interval_s=None,
                notes=None,
            )
        ],
    )
    run = load_run_sheet(path, limits)["pulse"]
    assert isinstance(run, RunSpec)
    assert run.sequence == bipolar_pulse_sequence(1e-4, 0.1, 100)
    assert run.start_mode == "immediate"
    assert run.sample_id is run.notes is None
    assert run.row["repeats"] is run.row["start_mode"] is None
    assert run.row["notes"] is None
    with pytest.raises(FrozenInstanceError):
        run.measurement_id = "other"


def test_explicit_pulse_fields(tmp_path, limits):
    path = write_sheet(
        tmp_path,
        [
            pulse(
                repeats=3.0,
                rest_s=2,
                pre_rest_s=1,
                v_low=0.1,
                v_high=2,
                fast_interval_s=0.01,
                fast_window_s=0.2,
                interval_s=0.2,
                start_mode="external",
                sample_id="sample α",
                notes="run notes",
            )
        ],
    )
    run = load_run_sheet(path, limits)["pulse"]
    assert run.sequence == bipolar_pulse_sequence(
        1e-4,
        0.1,
        3,
        rest_s=2,
        pre_rest_s=1,
        v_min=0.1,
        v_max=2,
        fast_interval_s=0.01,
        fast_window_s=0.2,
        interval_s=0.2,
    )
    assert run.start_mode == "external"
    assert run.sample_id == "sample α"
    assert run.notes == "run notes"


def test_cycling_and_soc_builder_defaults_and_signed_soc(tmp_path, limits):
    rows = [
        dict(
            measurement_id="cycle",
            protocol="cycling",
            current_a=1e-4,
            v_low=0.1,
            v_high=2,
            cycles=2.0,
        ),
        dict(
            measurement_id="cv",
            protocol="cycling",
            current_a=1e-4,
            v_low=0.1,
            v_high=2,
            cycles=1,
            cv_i_abs_min_a=1e-5,
            rest_s=5,
            max_step_s=10,
        ),
        dict(
            measurement_id="soc",
            protocol="soc_position",
            current_a=-1e-4,
            target_v=0.1,
            max_step_s=10,
            rest_s=5,
        ),
        dict(measurement_id="rest", protocol="rest", rest_s=10),
    ]
    runs = load_run_sheet(write_sheet(tmp_path, rows), limits)
    assert runs["cycle"].sequence == cycling_sequence(1e-4, 0.1, 2, 2)
    assert runs["cv"].sequence == cycling_sequence(
        1e-4,
        0.1,
        2,
        1,
        cv_i_abs_min=1e-5,
        rest_s=5,
        max_step_s=10,
    )
    assert runs["soc"].sequence == soc_position_sequence(-1e-4, 0.1, max_s=10, rest_s=5)
    assert runs["rest"].sequence.steps[0].sampling == Sampling(1)
    assert runs["rest"].sequence.steps[0].duration_s == 10


@pytest.mark.parametrize("protocol", ["cycling", "soc_position", "rest"])
def test_sampling_overrides_apply_to_all_steps(tmp_path, limits, protocol):
    row = dict(
        measurement_id="run",
        protocol=protocol,
        current_a=1e-4,
        v_low=0.1,
        v_high=2,
        cycles=1,
        target_v=1,
        max_step_s=10,
        rest_s=0.1,
        fast_interval_s=0.01,
        fast_window_s=0.5,
        interval_s=0.2,
    )
    run = load_run_sheet(write_sheet(tmp_path, [row]), limits)["run"]
    for step in run.sequence.steps:
        assert step.sampling == (
            Sampling(0.01) if step.duration_s == 0.1 else Sampling(0.2, 0.01, 0.5)
        )


@pytest.mark.parametrize("field,value", [("fast_interval_s", 0.01), ("fast_window_s", 1)])
def test_single_fast_field_uses_default_partner(tmp_path, limits, field, value):
    row = dict(measurement_id="rest", protocol="rest", rest_s=10) | {field: value}
    sampling = (
        load_run_sheet(write_sheet(tmp_path, [row]), limits)["rest"].sequence.steps[0].sampling
    )
    assert sampling == (
        Sampling(1, 0.01, 0.5) if field == "fast_interval_s" else Sampling(1, 0.006, 1)
    )


def test_extra_metadata_nan_and_text_ids(tmp_path, limits):
    rows = [
        pulse(
            measurement_id="001",
            sample_id="NA",
            notes="NULL",
            operator="Ada",
            missing=float("nan"),
            recorded=datetime(2026, 11, 3, 10, 30),
        ),
        {},
        pulse(measurement_id="NA", operator="Bob"),
    ]
    runs = load_run_sheet(write_sheet(tmp_path, rows), limits)
    assert list(runs) == ["001", "NA"]
    assert runs["NA"].row_number == 4
    run = runs["001"]
    assert run.sample_id == "NA"
    assert run.notes == "NULL"
    assert run.row["operator"] == "Ada"
    assert run.row["missing"] is None
    assert run.row["recorded"] == "2026-11-03T10:30:00"
    assert run.row["measurement_id"] == "001"
    json.dumps(run.row, allow_nan=False)


def test_errors_aggregate_rows_and_columns(tmp_path, limits):
    rows = [
        pulse(measurement_id="good"),
        pulse(measurement_id="bad", current_a=0.02),
        pulse(measurement_id="missing", pulse_s=None, current_a="oops"),
        pulse(measurement_id=None),
        dict(measurement_id="weird", protocol="unknown"),
    ]
    with pytest.raises(ValidationError) as raised:
        load_run_sheet(write_sheet(tmp_path, rows), limits)
    message = str(raised.value)
    assert "row 3 (bad): current_a: " in message
    assert "i_max_abs" in message
    assert "row 4 (missing): pulse_s: required value is blank" in message
    assert "row 4 (missing): current_a: must be a finite number" in message
    assert "row 5 (<missing>): measurement_id: required value is blank" in message
    assert "row 6 (weird): protocol: must be one of" in message
    assert "row 2" not in message


def test_duplicate_ids_report_both_rows(tmp_path, limits):
    with pytest.raises(ValidationError) as raised:
        load_run_sheet(write_sheet(tmp_path, [pulse(), pulse()]), limits)
    assert str(raised.value).splitlines() == [
        "row 2 (pulse): measurement_id: duplicate identifier",
        "row 3 (pulse): measurement_id: duplicate identifier",
    ]


def test_stray_spaces_do_not_create_distinct_ids(tmp_path, limits):
    runs = load_run_sheet(
        write_sheet(tmp_path, [pulse(measurement_id=" a ", protocol="bipolar_pulse ")]), limits
    )
    assert list(runs) == ["a"]
    with pytest.raises(ValidationError, match="duplicate identifier"):
        load_run_sheet(
            write_sheet(tmp_path, [pulse(measurement_id="a "), pulse(measurement_id="a")]), limits
        )


@pytest.mark.parametrize("start_mode", ["ttl", "Immediate", 1, True])
def test_start_mode_validation(tmp_path, limits, start_mode):
    with pytest.raises(ValidationError, match=r"row 2 \(pulse\): start_mode:"):
        load_run_sheet(write_sheet(tmp_path, [pulse(start_mode=start_mode)]), limits)


@pytest.mark.parametrize(
    "changes,column",
    [
        ({"measurement_id": 123}, "measurement_id"),
        ({"measurement_id": "   "}, "measurement_id"),
        ({"protocol": None}, "protocol"),
        ({"protocol": 2}, "protocol"),
        ({"repeats": 1.5}, "repeats"),
        ({"repeats": True}, "repeats"),
        ({"repeats": 0}, "repeats"),
        ({"current_a": "0.001"}, "current_a"),
        ({"current_a": -0.001}, "current_a"),
        ({"pulse_s": 0}, "pulse_s"),
        ({"rest_s": -1}, "rest_s"),
        ({"pre_rest_s": 0}, "pre_rest_s"),
        ({"fast_interval_s": 0.2}, "fast_interval_s"),
        ({"sample_id": 123}, "sample_id"),
        ({"notes": True}, "notes"),
    ],
)
def test_invalid_typed_values(tmp_path, limits, changes, column):
    with pytest.raises(ValidationError, match=f"{column}:"):
        load_run_sheet(write_sheet(tmp_path, [pulse(**changes)]), limits)


@pytest.mark.parametrize(
    "protocol,required",
    [
        ("bipolar_pulse", ["current_a", "pulse_s"]),
        ("cycling", ["current_a", "v_low", "v_high", "cycles"]),
        ("soc_position", ["current_a", "target_v", "max_step_s", "rest_s"]),
        ("rest", ["rest_s"]),
    ],
)
def test_missing_protocol_specific_fields(tmp_path, limits, protocol, required):
    row = dict(measurement_id="run", protocol=protocol)
    with pytest.raises(ValidationError) as raised:
        load_run_sheet(write_sheet(tmp_path, [row]), limits)
    for column in required:
        assert f"row 2 (run): {column}: required value is blank" in str(raised.value)


@pytest.mark.parametrize(
    "changes",
    [
        {"max_duration_s": 1},
        {"max_records": 1},
        {"min_interval_s": 0.01},
        {"v_min_abs": 0.5},
    ],
)
def test_validates_against_session_limits(tmp_path, limits, changes):
    with pytest.raises(ValidationError, match=r"row 2 \(pulse\):"):
        load_run_sheet(
            write_sheet(tmp_path, [pulse(v_low=0.1, v_high=2)]), replace(limits, **changes)
        )


@pytest.mark.parametrize(
    "headers",
    [
        ["protocol", "measurement_id"],
        ["measurement_id", "measurement_id", "protocol"],
        ["measurement_id"],
        ["protocol"],
        ["measurement_id", "protocol", None],
    ],
)
def test_malformed_headers(tmp_path, limits, headers):
    path = write_sheet(tmp_path, [["data"] * len(headers)], columns=headers)
    with pytest.raises(ValidationError, match=r"row 1 \(<header>\):"):
        load_run_sheet(path, limits)


def test_empty_workbook_and_header_only(tmp_path, limits):
    path = write_sheet(tmp_path, [])
    with pytest.raises(ValidationError, match="missing header"):
        load_run_sheet(path, limits)
    path = write_sheet(tmp_path, [], columns=["measurement_id", "protocol"])
    assert load_run_sheet(path, limits) == {}


def test_start_with_and_cv_after_columns(tmp_path, limits):
    rows = [
        pulse(measurement_id="p", start_with="discharge"),
        dict(
            measurement_id="ref",
            protocol="cycling",
            current_a=2e-5,
            v_low=0.005,
            v_high=0.2,
            cycles=1,
            cv_i_abs_min_a=4e-6,
            start_with=" discharge ",
            cv_after="discharge",
        ),
        dict(
            measurement_id="plain",
            protocol="cycling",
            current_a=2e-5,
            v_low=0.005,
            v_high=0.2,
            cycles=1,
            cv_i_abs_min_a=4e-6,
        ),
    ]
    runs = load_run_sheet(write_sheet(tmp_path, rows), limits)
    assert runs["p"].sequence.steps[1].level < 0 < runs["p"].sequence.steps[3].level
    assert runs["ref"].sequence == cycling_sequence(
        2e-5, 0.005, 0.2, 1, cv_i_abs_min=4e-6, start="discharge", cv_after="discharge"
    )
    assert runs["plain"].sequence == cycling_sequence(2e-5, 0.005, 0.2, 1, cv_i_abs_min=4e-6)


@pytest.mark.parametrize("column", ["start_with", "cv_after"])
def test_start_with_and_cv_after_values_are_checked(tmp_path, limits, column):
    with pytest.raises(ValidationError, match=f"{column}: must be charge or discharge"):
        load_run_sheet(write_sheet(tmp_path, [pulse(**{column: "sodiation"})]), limits)


def test_template_documents_the_new_columns(tmp_path):
    sheet = load_workbook(make_template(tmp_path / "runs.xlsx"))["Runs"]
    header = [cell.value for cell in sheet[1]]
    assert {"start_with", "cv_after"} <= set(header)


def test_template_command_writes_once(tmp_path, capsys):
    from ophyd_electrochemistry.sequences.run_sheet import template_main

    path = tmp_path / "runs.xlsx"
    assert template_main([str(path)]) == 0
    assert capsys.readouterr().out.strip() == str(path)
    before = path.read_bytes()
    with pytest.raises(SystemExit):
        template_main([str(path)])
    assert path.read_bytes() == before
