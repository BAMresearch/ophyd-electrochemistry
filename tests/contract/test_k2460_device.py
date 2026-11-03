"""RunEngine coverage for the first operational M6 Flyer slice."""

import json
import threading
import time
from dataclasses import replace

import event_model
import pytest
from bluesky import RunEngine
from bluesky import plan_stubs as bps

from examples.compile_program import capabilities as example_capabilities
from examples.compile_program import config as example_config
from examples.follower import repeated_single_pulse_acquisitions
from ophyd_electrochemistry import (
    AcquisitionRequest,
    CurrentPulseSequence,
    CyclicVoltammetry,
    DeviceState,
    FieldOrigin,
    GalvanostaticHold,
    MappingQuality,
    MeasurementAperture,
    RelativeTimeOrigin,
    StartMode,
    TimestampReference,
    TimingOrigin,
    VoltagePulseSequence,
)
from ophyd_electrochemistry.exceptions import (
    AcquisitionAborted,
    RetainedDataError,
    UnsupportedCapabilityError,
    ValidationError,
)
from ophyd_electrochemistry.keithley.k2460 import Keithley2460Device
from ophyd_electrochemistry.simulation import FakeCell, FakeTransport, SimulatedRuntime


def request(mode: StartMode = StartMode.IMMEDIATE) -> AcquisitionRequest:
    return AcquisitionRequest(
        experiment_id="m6-device-contract",
        start_mode=mode,
        program=GalvanostaticHold(
            current_a=0.01,
            duration_s=0.04,
            sample_period_s=0.01,
            voltage_limit_v=5,
        ),
    )


def pulse_request(mode: StartMode = StartMode.IMMEDIATE, *, count: int = 2) -> AcquisitionRequest:
    return AcquisitionRequest(
        experiment_id="m6-pulse-contract",
        start_mode=mode,
        program=CurrentPulseSequence(
            baseline_current_a=0,
            pulse_current_a=0.01,
            pulse_width_s=0.01,
            period_s=0.03,
            count=count,
            sample_period_s=0.006,
            voltage_limit_v=5,
        ),
    )


def device_stack(
    *, completion_timeout_s: float = 1, command_timeout_s: float = 1
) -> tuple[Keithley2460Device, SimulatedRuntime, FakeTransport]:
    config = replace(
        example_config,
        timing=replace(
            example_config.timing,
            command_timeout_s=command_timeout_s,
            external_start_timeout_s=0.03,
            poll_period_s=0.001,
        ),
    )
    runtime = SimulatedRuntime(
        capabilities=example_capabilities,
        config=config,
        cell=FakeCell(resistance_ohm=10, open_circuit_voltage_v=0.1),
    )
    backend = FakeTransport(runtime)
    ids = iter(("m6-acquisition-1", "m6-acquisition-2"))
    device = Keithley2460Device(
        backend,
        name="ec",
        poll_period_s=0.001,
        completion_timeout_s=completion_timeout_s,
        shutdown_timeout_s=1,
        collection_chunk_records=2,
        acquisition_id_factory=lambda: next(ids),
    )
    return device, runtime, backend


def wait_for_state(device: Keithley2460Device, state: DeviceState) -> None:
    deadline = time.monotonic() + 2
    while device.state != state and time.monotonic() < deadline:
        time.sleep(0.001)
    assert device.state == state


@pytest.mark.parametrize("mode", list(StartMode))
def test_real_runengine_operates_device_and_emits_fixed_m5_schema(mode):
    device, runtime, backend = device_stack()
    chunk_requests = []
    read_record_chunk = backend.read_record_chunk

    def tracked_chunk(*, offset, max_records):
        chunk_requests.append((offset, max_records))
        return read_record_chunk(offset=offset, max_records=max_records)

    backend.read_record_chunk = tracked_chunk
    documents = []
    run_engine = RunEngine({})
    run_engine.subscribe(lambda name, doc: documents.append((name, doc)))
    worker_errors = []

    def finish_instrument_owned_program():
        try:
            expected = (
                DeviceState.WAITING_START
                if mode == StartMode.EXTERNAL_TRIGGER
                else DeviceState.RUNNING
            )
            wait_for_state(device, expected)
            if mode == StartMode.EXTERNAL_TRIGGER:
                assert runtime.output is False and runtime.ready
                runtime.set_inputs(start=True, abort=False)
            runtime.advance_ticks(40)
        except BaseException as exc:
            worker_errors.append(exc)

    worker = threading.Thread(target=finish_instrument_owned_program, daemon=True)
    worker.start()

    def plan():
        yield from bps.open_run(md={"experiment_id": "m6-device-contract"})
        yield from bps.stage(device)
        yield from bps.prepare(device, request(mode), wait=True)
        yield from bps.kickoff(device, wait=True)
        yield from bps.complete(device, wait=True)
        yield from bps.collect(device, return_payload=False)
        yield from bps.unstage(device)
        yield from bps.close_run()

    try:
        run_engine(plan())
    finally:
        worker.join(timeout=2)
    assert not worker.is_alive() and not worker_errors
    assert device.state == DeviceState.COMPLETE
    assert device.output_enabled is False
    assert list(device.collect()) == []

    for name, document in documents:
        event_model.schema_validators[event_model.DocumentNames(name)].validate(document)
    descriptor = next(document for name, document in documents if name == "descriptor")
    assert descriptor["name"] == "electrochemistry"
    assert set(descriptor["data_keys"]) == {
        "ec_sample_index",
        "ec_time_relative",
        "ec_instrument_timestamp",
        "ec_voltage_aperture_start",
        "ec_voltage_aperture_end",
        "ec_current_aperture_start",
        "ec_current_aperture_end",
        "ec_voltage_timing_origin",
        "ec_current_timing_origin",
        "ec_voltage_timing_uncertainty",
        "ec_current_timing_uncertainty",
        "ec_available",
        "ec_voltage",
        "ec_current",
        "ec_source_function",
        "ec_source_setpoint",
        "ec_source_status",
        "ec_measurement_status",
        "ec_mapping_quality",
        "ec_first_logical_point",
        "ec_last_logical_point",
        "ec_repeat_index",
        "ec_point_index",
        "ec_cycle_index",
        "ec_segment_index",
    }
    event_page = next(document for name, document in documents if name == "event_page")
    assert event_page["data"]["ec_sample_index"] == [0, 1, 2, 3]
    assert event_page["data"]["ec_source_function"] == ["current"] * 4
    assert event_page["data"]["ec_current"] == pytest.approx([0.01] * 4)
    assert event_page["data"]["ec_source_status"] == [0] * 4
    assert event_page["data"]["ec_measurement_status"] == [0] * 4
    assert event_page["data"]["ec_voltage_aperture_start"] == pytest.approx(
        event_page["data"]["ec_time_relative"]
    )
    assert (
        event_page["data"]["ec_voltage_aperture_end"]
        == (event_page["data"]["ec_current_aperture_end"])
    )
    for field in ("voltage", "current"):
        assert event_page["data"][f"ec_{field}_timing_origin"] == ["synthetic"] * 4
        assert event_page["data"][f"ec_{field}_timing_uncertainty"] == [0] * 4
    configuration = descriptor["configuration"]["ec"]["data"]
    assert configuration["ec_measurement_schema"] == "ophyd-electrochemistry/measurement-v4"
    assert configuration["ec_timestamp_reference"] == "aperture_start"
    assert configuration["ec_timestamp_field"] == "voltage"
    assert configuration["ec_relative_time_origin"] == "actual_start"
    assert configuration["ec_actual_start_known"] == 1
    assert "compliance" in configuration["ec_source_status_definition"]
    assert "no measurement-status bits" in configuration["ec_measurement_status_definition"]
    expected_bound = 0.04 + (0.03 if mode == StartMode.EXTERNAL_TRIGGER else 0)
    assert configuration["ec_completion_timeout"] == 1
    assert configuration["ec_prepared_completion_bound"] == pytest.approx(expected_bound)
    assert configuration["ec_prepared_communication_margin"] == 1
    assert configuration["ec_completion_timeout_effective"] == pytest.approx(expected_bound + 1)
    assert configuration["ec_prepared_request_sha256"] == runtime.retained_buffer().request_sha256
    assert configuration["ec_prepared_program_sha256"] == runtime.retained_buffer().program_sha256
    assert configuration["ec_prepared_expected_records"] == 4
    assert configuration["ec_prepared_capacity_records"] == 20_000
    assert chunk_requests == [(0, 2), (2, 2)]
    assert device.read_configuration()["ec_start_edge"]["value"] == "rising"


def test_uncalibrated_first_reading_clock_is_explicit_in_flyer(monkeypatch):
    device, runtime, _ = device_stack()
    device.stage()
    device.prepare(request()).wait(timeout=1)
    device.kickoff().wait(timeout=1)
    runtime.advance_ticks(40)
    device.complete().wait(timeout=1)
    original = runtime.retained_buffer()
    first = original.records[0].instrument_timestamp_s
    records = tuple(
        replace(
            record,
            instrument_timestamp_s=record.instrument_timestamp_s - first,
            time_relative_s=record.instrument_timestamp_s - first,
            voltage_aperture=None,
            current_aperture=None,
            available_relative_s=None,
            mapping_quality=MappingQuality.UNKNOWN,
            first_logical_point=None,
            last_logical_point=None,
            repeat_index=None,
            point_index=None,
            cycle_index=None,
            segment_index=None,
        )
        for record in original.records
    )
    retained = replace(
        original,
        schema=replace(
            original.schema,
            voltage_origin=FieldOrigin.MEASURED,
            current_origin=FieldOrigin.SOURCE_READBACK,
            timing_origin=TimingOrigin.INSTRUMENT_REPORTED,
            timestamp_reference=TimestampReference.UNRESOLVED,
            timestamp_field=None,
            instrument_timestamp_origin="relative-to-first-buffer-reading",
            instrument_start_timestamp_s=None,
            instrument_timestamp_resolution_s=None,
            relative_time_origin=RelativeTimeOrigin.FIRST_STORED_READING,
            synthetic=False,
        ),
        records=records,
    )
    monkeypatch.setattr(runtime, "retained_buffer", lambda: retained)
    device.describe_collect()
    configuration = device.read_configuration()
    assert configuration["ec_relative_time_origin"]["value"] == "first_stored_reading"
    assert configuration["ec_actual_start_known"]["value"] == 0
    assert configuration["ec_instrument_timestamp_origin"]["value"] == (
        "relative-to-first-buffer-reading"
    )
    events = list(device.collect())
    assert events[0]["data"]["ec_time_relative"] == 0
    for record, event in zip(records, events, strict=True):
        assert event["data"]["ec_instrument_timestamp"] == record.instrument_timestamp_s
        assert event["data"]["ec_voltage_aperture_start"] == -1
        assert event["data"]["ec_current_aperture_start"] == -1
        assert event["data"]["ec_available"] == -1
        assert event["data"]["ec_mapping_quality"] == "unknown"
        assert event["time"] > 1_000_000_000  # Host envelope, never the raw buffer clock.


@pytest.mark.parametrize(
    ("timestamp_reference", "known_fields"),
    [
        (TimestampReference.BUFFER_STORED, ("voltage", "current")),
        (TimestampReference.UNRESOLVED, ("voltage",)),
        (TimestampReference.UNRESOLVED, ("current",)),
        (TimestampReference.UNRESOLVED, ()),
    ],
)
def test_field_timing_projection_preserves_independent_windows_and_unknowns(
    monkeypatch, timestamp_reference, known_fields
):
    device, runtime, _ = device_stack()
    assert device.read_configuration()["ec_timestamp_reference"]["value"] == "unknown"
    assert device.read_configuration()["ec_timestamp_field"]["value"] == "unknown"
    device.stage()
    device.prepare(request()).wait(timeout=1)
    device.kickoff().wait(timeout=1)
    runtime.advance_ticks(40)
    device.complete().wait(timeout=1)
    original = runtime.retained_buffer()
    voltage_aperture = MeasurementAperture(
        start_relative_s=0.001,
        end_relative_s=0.002,
        origin=TimingOrigin.INSTRUMENT_REPORTED,
        uncertainty_s=0.0001,
    )
    current_aperture = MeasurementAperture(
        start_relative_s=0,
        end_relative_s=0.0005,
        origin=TimingOrigin.DERIVED_FROM_CONFIGURATION,
        uncertainty_s=None,
    )
    records = tuple(
        replace(
            record,
            voltage_aperture=voltage_aperture if "voltage" in known_fields else None,
            current_aperture=current_aperture if "current" in known_fields else None,
            available_relative_s=None,
            source_status=200,
            measurement_status=8,
            mapping_quality=MappingQuality.UNKNOWN,
            first_logical_point=None,
            last_logical_point=None,
            repeat_index=None,
            point_index=None,
            cycle_index=None,
            segment_index=None,
        )
        for record in original.records
    )
    retained = replace(
        original,
        schema=replace(
            original.schema,
            voltage_origin=FieldOrigin.MEASURED,
            current_origin=FieldOrigin.SOURCE_READBACK,
            timing_origin=TimingOrigin.DERIVED_FROM_CONFIGURATION,
            timestamp_reference=timestamp_reference,
            timestamp_field=None,
            synthetic=False,
        ),
        records=records,
    )
    monkeypatch.setattr(runtime, "retained_buffer", lambda: retained)
    descriptor = device.describe_collect()["electrochemistry"]
    events = list(device.collect())
    assert len(events) == 4
    for event in events:
        data = event["data"]
        assert set(data) == set(descriptor)
        assert data["ec_available"] == -1
        assert data["ec_source_status"] == 200 and data["ec_measurement_status"] == 8
        assert data["ec_mapping_quality"] == "unknown"
        for suffix in (
            "first_logical_point",
            "last_logical_point",
            "repeat_index",
            "point_index",
            "cycle_index",
            "segment_index",
        ):
            assert data[f"ec_{suffix}"] == -1
        for field, aperture in (("voltage", voltage_aperture), ("current", current_aperture)):
            known = field in known_fields
            assert data[f"ec_{field}_aperture_start"] == (
                aperture.start_relative_s if known else -1
            )
            assert data[f"ec_{field}_aperture_end"] == (aperture.end_relative_s if known else -1)
            assert data[f"ec_{field}_timing_origin"] == (
                aperture.origin.value if known else "unknown"
            )
            assert data[f"ec_{field}_timing_uncertainty"] == (
                aperture.uncertainty_s if known and aperture.uncertainty_s is not None else -1
            )
            assert descriptor[f"ec_{field}_timing_uncertainty"]["units"] == "s"
    configuration = device.read_configuration()
    assert configuration["ec_timestamp_reference"]["value"] == timestamp_reference.value
    assert configuration["ec_timestamp_field"]["value"] == "unknown"
    device.trigger().wait(timeout=1)
    snapshot = {key: value["value"] for key, value in device.read().items()}
    assert snapshot == events[-1]["data"]
    device.unstage()


def test_abort_fails_completion_preserves_partial_data_and_recovers():
    device, runtime, _ = device_stack()
    device.stage()
    device.prepare(request()).wait(timeout=1)
    device.kickoff().wait(timeout=1)
    runtime.advance_ticks(15)
    completion = device.complete()
    assert device.complete() is completion

    device.abort(reason="contract-test").wait(timeout=1)
    with pytest.raises(AcquisitionAborted, match="contract-test"):
        completion.wait(timeout=1)
    assert device.state == DeviceState.ABORTED and device.output_enabled is False

    retained_events = list(device.collect())
    assert [event["data"]["ec_sample_index"] for event in retained_events] == [0, 1]
    device.trigger().wait(timeout=1)
    snapshot = device.read()
    assert snapshot["ec_sample_index"]["value"] == 1
    assert device.describe()["ec_voltage"]["units"] == "V"

    device.recover().wait(timeout=1)
    assert device.state == DeviceState.IDLE and device.output_enabled is False
    device.unstage()


def test_recovery_before_collection_preserves_partial_data_and_explicit_disposition():
    device, runtime, _ = device_stack()
    device.stage()
    device.prepare(request()).wait(timeout=1)
    acquisition_id = device.acquisition_id
    assert acquisition_id is not None
    device.kickoff().wait(timeout=1)
    runtime.advance_ticks(15)
    completion = device.complete()
    device.abort(reason="recover-before-collect").wait(timeout=1)
    device.recover().wait(timeout=1)
    assert device.state == DeviceState.IDLE and device.output_enabled is False
    with pytest.raises(AcquisitionAborted):
        completion.wait(timeout=1)
    assert [event["data"]["ec_sample_index"] for event in device.collect()] == [0, 1]
    device.trigger().wait(timeout=1)
    assert device.read()["ec_sample_index"]["value"] == 1
    with pytest.raises(RetainedDataError):
        device.discard_retained_data(acquisition_id="wrong", reason="test")
    device.discard_retained_data(acquisition_id=acquisition_id, reason="explicit-test-disposition")
    with pytest.raises(RetainedDataError):
        device.describe_collect()
    device.unstage()


@pytest.mark.parametrize("mode", list(StartMode))
def test_one_start_runs_the_complete_instrument_timed_pulse_train(mode):
    device, runtime, _ = device_stack()
    documents = []
    run_engine = RunEngine({})
    run_engine.subscribe(lambda name, doc: documents.append((name, doc)))
    worker_errors = []

    def finish_pulse_train():
        try:
            expected = (
                DeviceState.WAITING_START
                if mode == StartMode.EXTERNAL_TRIGGER
                else DeviceState.RUNNING
            )
            wait_for_state(device, expected)
            if mode == StartMode.EXTERNAL_TRIGGER:
                runtime.set_inputs(start=True, abort=False)
            runtime.advance_ticks(60)
        except BaseException as exc:
            worker_errors.append(exc)

    worker = threading.Thread(target=finish_pulse_train, daemon=True)
    worker.start()

    def plan():
        yield from bps.open_run(md={"experiment_id": "m6-pulse-contract"})
        yield from bps.stage(device)
        yield from bps.prepare(device, pulse_request(mode), wait=True)
        yield from bps.kickoff(device, wait=True)
        yield from bps.complete(device, wait=True)
        yield from bps.collect(device, return_payload=False)
        yield from bps.unstage(device)
        yield from bps.close_run()

    try:
        run_engine(plan())
    finally:
        worker.join(timeout=2)

    assert not worker.is_alive() and not worker_errors
    assert [(step.relative_tick, step.level) for step in runtime.source_trace] == [
        (0, 0.01),
        (10, 0),
        (30, 0.01),
        (40, 0),
    ]
    event_page = next(document for name, document in documents if name == "event_page")
    assert len(event_page["data"]["ec_sample_index"]) == 10
    assert device.state == DeviceState.COMPLETE and device.output_enabled is False


def test_trigger_per_pulse_uses_distinct_rearmed_acquisitions():
    device, runtime, _ = device_stack()
    acquisition_ids = []
    device.stage()

    for shot in range(2):
        device.prepare(pulse_request(StartMode.EXTERNAL_TRIGGER, count=1)).wait(timeout=1)
        acquisition_id = device.acquisition_id
        assert acquisition_id is not None
        acquisition_ids.append(acquisition_id)
        device.kickoff().wait(timeout=1)
        assert device.state == DeviceState.WAITING_START
        assert runtime.output is False and runtime.source_trace == ()

        runtime.set_inputs(start=True, abort=False)
        runtime.advance_ticks(30)
        device.complete().wait(timeout=1)
        assert [(step.relative_tick, step.level) for step in runtime.source_trace] == [
            (0, 0.01),
            (10, 0),
        ]
        assert len(list(device.collect())) == 5

        device.discard_retained_data(
            acquisition_id=acquisition_id, reason=f"contract-shot-{shot}-collected"
        )
        runtime.set_inputs(start=False, abort=False)

    assert acquisition_ids == ["m6-acquisition-1", "m6-acquisition-2"]
    device.unstage()


def test_repeated_pulse_plan_archives_before_discard_and_rearm(tmp_path):
    device, runtime, _ = device_stack()
    destinations = [tmp_path / "shot-0.json", tmp_path / "shot-1.json"]
    documents = []
    worker_errors = []
    run_engine = RunEngine({})
    run_engine.subscribe(lambda name, doc: documents.append((name, doc)))

    def trigger_each_armed_shot():
        try:
            for _ in destinations:
                wait_for_state(device, DeviceState.WAITING_START)
                runtime.set_inputs(start=True, abort=False)
                runtime.advance_ticks(30)
                runtime.set_inputs(start=False, abort=False)
                wait_for_state(device, DeviceState.COMPLETE)
        except BaseException as exc:
            worker_errors.append(exc)

    worker = threading.Thread(target=trigger_each_armed_shot, daemon=True)
    worker.start()
    try:
        run_engine(
            repeated_single_pulse_acquisitions(
                device,
                pulse_request(StartMode.EXTERNAL_TRIGGER, count=1),
                destinations,
            )
        )
    finally:
        worker.join(timeout=2)

    assert not worker.is_alive() and not worker_errors
    assert [document["shot_index"] for name, document in documents if name == "start"] == [0, 1]
    assert [name for name, _ in documents].count("stop") == 2
    assert runtime.dispositions == [
        ("m6-acquisition-1", "shot 0 raw archive exported"),
        ("m6-acquisition-2", "shot 1 raw archive exported"),
    ]
    archived_ids = [
        json.loads(path.read_text())["payload"]["acquisition_id"] for path in destinations
    ]
    assert archived_ids == ["m6-acquisition-1", "m6-acquisition-2"]


def test_repeated_pulse_plan_preserves_data_when_archive_exists(tmp_path):
    device, runtime, _ = device_stack()
    destination = tmp_path / "occupied.json"
    destination.write_text("operator-owned\n")
    run_engine = RunEngine({})

    def trigger_armed_shot():
        wait_for_state(device, DeviceState.WAITING_START)
        runtime.set_inputs(start=True, abort=False)
        runtime.advance_ticks(30)
        runtime.set_inputs(start=False, abort=False)

    worker = threading.Thread(target=trigger_armed_shot, daemon=True)
    worker.start()
    try:
        with pytest.raises(RetainedDataError, match="already exists"):
            run_engine(
                repeated_single_pulse_acquisitions(
                    device,
                    pulse_request(StartMode.EXTERNAL_TRIGGER, count=1),
                    [destination],
                )
            )
    finally:
        worker.join(timeout=2)

    assert not worker.is_alive()
    assert destination.read_text() == "operator-owned\n"
    assert runtime.dispositions == []
    assert len(runtime.records) == 5
    assert runtime.state == DeviceState.COMPLETE and runtime.output is False


def test_no_start_timeout_fails_completion_with_empty_collectable_buffer():
    device, runtime, _ = device_stack()
    device.stage()
    device.prepare(request(StartMode.EXTERNAL_TRIGGER)).wait(timeout=1)
    device.kickoff().wait(timeout=1)
    completion = device.complete()
    runtime.advance_ticks(30)

    with pytest.raises(AcquisitionAborted, match="start_timeout"):
        completion.wait(timeout=1)
    assert device.state == DeviceState.ABORTED and device.output_enabled is False
    assert device.describe_collect()["electrochemistry"]
    assert list(device.collect()) == []
    device.recover().wait(timeout=1)
    device.unstage()


@pytest.mark.parametrize(
    ("mode", "expected_bound"),
    [
        (StartMode.IMMEDIATE, 0.04),
        (StartMode.EXTERNAL_TRIGGER, 0.07),
    ],
)
def test_prepare_derives_per_request_completion_watchdog(mode, expected_bound):
    device, _, _ = device_stack(completion_timeout_s=0.001, command_timeout_s=0.002)
    device.stage()

    before = device.read_configuration()
    assert before["ec_completion_timeout_effective"]["value"] == 0
    device.prepare(request(mode)).wait(timeout=1)
    after = device.read_configuration()

    assert after["ec_completion_timeout"]["value"] == 0.001
    assert after["ec_prepared_completion_bound"]["value"] == pytest.approx(expected_bound)
    assert after["ec_prepared_communication_margin"]["value"] == 0.002
    assert after["ec_completion_timeout_effective"]["value"] == pytest.approx(
        expected_bound + 0.002
    )
    device.abort(reason="test-cleanup").wait(timeout=1)
    device.unstage()


def test_derived_watchdog_prevents_short_static_minimum_from_aborting_longer_run():
    device, runtime, _ = device_stack(completion_timeout_s=0.001, command_timeout_s=0.001)
    device.stage()
    device.prepare(request()).wait(timeout=1)
    device.kickoff().wait(timeout=1)
    completion = device.complete()

    time.sleep(0.01)  # Longer than the configured minimum, shorter than the derived bound.
    runtime.advance_ticks(40)
    completion.wait(timeout=1)

    assert device.state == DeviceState.COMPLETE and device.output_enabled is False
    device.unstage()


def test_effective_watchdog_is_replaced_for_each_successive_request():
    device, runtime, _ = device_stack(completion_timeout_s=0.001, command_timeout_s=0.001)
    device.stage()
    effective = []

    for value, terminal_ticks in ((request(), 40), (pulse_request(count=10), 300)):
        device.prepare(value).wait(timeout=1)
        effective.append(device.read_configuration()["ec_completion_timeout_effective"]["value"])
        device.kickoff().wait(timeout=1)
        runtime.advance_ticks(terminal_ticks)
        device.complete().wait(timeout=1)
        acquisition_id = device.acquisition_id
        assert acquisition_id is not None
        device.discard_retained_data(
            acquisition_id=acquisition_id, reason="successive-request-contract"
        )

    assert effective == pytest.approx([0.042, 0.302])
    device.unstage()


def test_failed_prepare_clears_previous_effective_watchdog():
    device, runtime, backend = device_stack(completion_timeout_s=0.001, command_timeout_s=0.001)
    device.stage()
    device.prepare(request()).wait(timeout=1)
    first = device.read_configuration()["ec_completion_timeout_effective"]["value"]
    device.kickoff().wait(timeout=1)
    runtime.advance_ticks(40)
    device.complete().wait(timeout=1)
    acquisition_id = device.acquisition_id
    assert acquisition_id is not None
    device.discard_retained_data(acquisition_id=acquisition_id, reason="contract-test")

    prepare = backend.prepare

    def malformed_prepare(value, *, acquisition_id):
        prepare(value, acquisition_id=acquisition_id)
        return object()

    backend.prepare = malformed_prepare
    with pytest.raises(ValidationError, match="PreparedAcquisition"):
        device.prepare(request()).wait(timeout=1)

    configuration = device.read_configuration()
    assert first > 0
    assert configuration["ec_completion_timeout_effective"]["value"] == 0
    assert configuration["ec_prepared_completion_bound"]["value"] == 0
    assert runtime.output is False
    device.stop(success=False)
    device.unstage()


def test_rejected_reprepare_preserves_the_retained_acquisition_identity_and_data():
    device, runtime, _ = device_stack()
    device.stage()
    device.prepare(request()).wait(timeout=1)
    original_id = device.acquisition_id
    device.kickoff().wait(timeout=1)
    runtime.advance_ticks(40)
    device.complete().wait(timeout=1)
    original_timeout = device.read_configuration()["ec_completion_timeout_effective"]["value"]

    with pytest.raises(RetainedDataError, match="discard retained acquisition"):
        device.prepare(request()).wait(timeout=1)

    assert device.state == DeviceState.COMPLETE
    assert device.acquisition_id == original_id
    assert (
        device.read_configuration()["ec_completion_timeout_effective"]["value"] == original_timeout
    )
    assert [event["data"]["ec_sample_index"] for event in device.collect()] == [0, 1, 2, 3]
    assert runtime.output is False
    device.unstage()


def test_prepare_rejects_a_result_for_a_different_request():
    device, runtime, backend = device_stack()
    device.stage()
    prepare = backend.prepare

    def wrong_request_prepare(value, *, acquisition_id):
        prepared = prepare(value, acquisition_id=acquisition_id)
        return replace(prepared, request_sha256="0" * 64)

    backend.prepare = wrong_request_prepare
    with pytest.raises(ValidationError, match="wrong acquisition request"):
        device.prepare(request()).wait(timeout=1)

    assert runtime.state == DeviceState.PREPARED and runtime.output is False
    assert device.read_configuration()["ec_completion_timeout_effective"]["value"] == 0
    device.stop(success=False)
    device.unstage()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("request_sha256", "0" * 64, "request digest"),
        ("program_sha256", "0" * 64, "program digest"),
        ("capacity_records", 20_001, "capacity"),
    ],
)
def test_collect_cross_checks_retained_metadata_against_prepare(field, value, message):
    device, runtime, backend = device_stack()
    device.stage()
    device.prepare(request()).wait(timeout=1)
    device.kickoff().wait(timeout=1)
    runtime.advance_ticks(40)
    device.complete().wait(timeout=1)
    retained_metadata = backend.retained_metadata

    def mismatched_metadata():
        return replace(retained_metadata(), **{field: value})

    backend.retained_metadata = mismatched_metadata
    with pytest.raises(RetainedDataError, match=message):
        list(device.collect())

    assert runtime.output is False
    device.unstage()


def test_narrow_slice_rejects_unimplemented_program_before_output():
    device, runtime, _ = device_stack()
    device.stage()
    unsupported = AcquisitionRequest(
        experiment_id="unsupported-cv",
        program=CyclicVoltammetry(
            start_voltage_v=0,
            first_vertex_v=0.1,
            second_vertex_v=-0.1,
            scan_rate_v_per_s=1,
            cycles=1,
            sample_period_s=0.01,
            current_limit_a=0.01,
        ),
    )
    with pytest.raises(UnsupportedCapabilityError, match="GalvanostaticHold"):
        device.prepare(unsupported).wait(timeout=1)
    unsupported_voltage_pulses = AcquisitionRequest(
        experiment_id="unsupported-voltage-pulses",
        program=VoltagePulseSequence(
            baseline_voltage_v=0,
            pulse_voltage_v=0.1,
            pulse_width_s=0.01,
            period_s=0.03,
            count=2,
            sample_period_s=0.006,
            current_limit_a=0.1,
        ),
    )
    with pytest.raises(UnsupportedCapabilityError, match="GalvanostaticHold"):
        device.prepare(unsupported_voltage_pulses).wait(timeout=1)
    assert runtime.state == DeviceState.IDLE and runtime.output is False
    before, after = device.configure({"collection_chunk_records": 1})
    assert before["ec_collection_chunk_records"]["value"] == 2
    assert after["ec_collection_chunk_records"]["value"] == 1
    with pytest.raises(UnsupportedCapabilityError, match="Only collection_chunk_records"):
        device.configure({"source_terminal": "front"})
    device.unstage()


def test_collect_rejects_a_backend_chunk_larger_than_requested():
    device, runtime, backend = device_stack()
    device.stage()
    device.prepare(request()).wait(timeout=1)
    device.kickoff().wait(timeout=1)
    runtime.advance_ticks(40)
    device.complete().wait(timeout=1)

    read_record_chunk = backend.read_record_chunk

    def oversized_chunk(*, offset, max_records):
        return read_record_chunk(offset=offset, max_records=max_records + 1)

    backend.read_record_chunk = oversized_chunk
    with pytest.raises(RetainedDataError, match="requested record limit"):
        list(device.collect())
    assert runtime.output is False
    device.unstage()
