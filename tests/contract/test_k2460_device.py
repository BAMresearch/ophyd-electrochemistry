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
    GalvanostaticHold,
    StartMode,
)
from ophyd_electrochemistry.exceptions import (
    AcquisitionAborted,
    RetainedDataError,
    UnsupportedCapabilityError,
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


def device_stack() -> tuple[Keithley2460Device, SimulatedRuntime, FakeTransport]:
    config = replace(
        example_config,
        timing=replace(
            example_config.timing,
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
        completion_timeout_s=1,
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
        "ec_aperture_start",
        "ec_aperture_end",
        "ec_available",
        "ec_voltage",
        "ec_current",
        "ec_source_function",
        "ec_source_setpoint",
        "ec_status_bits",
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
    assert chunk_requests == [(0, 2), (2, 2)]
    assert device.read_configuration()["ec_start_edge"]["value"] == "rising"


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
