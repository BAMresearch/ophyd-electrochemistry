"""RunEngine coverage for the first operational M6 Flyer slice."""

import threading
import time
from dataclasses import replace

import event_model
import pytest
from bluesky import RunEngine
from bluesky import plan_stubs as bps

from examples.compile_program import capabilities as example_capabilities
from examples.compile_program import config as example_config
from ophyd_electrochemistry import (
    AcquisitionRequest,
    CyclicVoltammetry,
    DeviceState,
    GalvanostaticHold,
    StartMode,
)
from ophyd_electrochemistry.exceptions import (
    AcquisitionAborted,
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


def device_stack() -> tuple[Keithley2460Device, SimulatedRuntime]:
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
        acquisition_id_factory=lambda: next(ids),
    )
    return device, runtime


def wait_for_state(device: Keithley2460Device, state: DeviceState) -> None:
    deadline = time.monotonic() + 2
    while device.state != state and time.monotonic() < deadline:
        time.sleep(0.001)
    assert device.state == state


@pytest.mark.parametrize("mode", list(StartMode))
def test_real_runengine_operates_device_and_emits_fixed_m5_schema(mode):
    device, runtime = device_stack()
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


def test_abort_fails_completion_preserves_partial_data_and_recovers():
    device, runtime = device_stack()
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


def test_no_start_timeout_fails_completion_with_empty_collectable_buffer():
    device, runtime = device_stack()
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
    device, runtime = device_stack()
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
    with pytest.raises(UnsupportedCapabilityError, match="no runtime-configurable"):
        device.configure({"source_terminal": "front"})
    device.unstage()
