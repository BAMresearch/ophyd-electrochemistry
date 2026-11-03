"""Offline lifecycle and ownership evidence for the timed resistor candidate."""

import json
import threading
from dataclasses import replace
from pathlib import Path

import event_model
import pytest
from bluesky import RunEngine
from bluesky import plan_stubs as bps

from ophyd_electrochemistry.acquisition import AcquisitionRequest, StartMode
from ophyd_electrochemistry.exceptions import (
    AcquisitionAborted,
    ElectrochemistryError,
    IncompatibleInstrumentError,
    RetainedDataError,
    ShutdownUnconfirmed,
    TransportProtocolError,
    TransportTimeoutError,
    UnsupportedCapabilityError,
    ValidationError,
)
from ophyd_electrochemistry.keithley.k2460 import backend as module
from ophyd_electrochemistry.keithley.k2460.backend import Keithley2460Backend
from ophyd_electrochemistry.keithley.k2460.config import (
    BufferPolicy,
    Keithley2460Config,
    SafetyConfig,
    TimingPolicy,
)
from ophyd_electrochemistry.keithley.k2460.device import Keithley2460Device
from ophyd_electrochemistry.keithley.k2460.measurement_profiles import K2460MeasurementProfile
from ophyd_electrochemistry.keithley.k2460.runtime import (
    RUNTIME_ABI,
    BufferedReading,
    RuntimeBufferInfo,
    RuntimeRecordChunk,
    RuntimeState,
    RuntimeStatus,
    _buffered_records_sha256,
)
from ophyd_electrochemistry.keithley.k2460.transport import VisaTransportConfig
from ophyd_electrochemistry.protocols import GalvanostaticHold, PotentiostaticHold
from ophyd_electrochemistry.serialization import canonical_json, request_sha256, sha256_json
from ophyd_electrochemistry.state import DeviceState


def status(state=RuntimeState.IDLE, *, output=False):
    active = state in (RuntimeState.RUNNING, RuntimeState.WAITING_START)
    return RuntimeStatus(
        abi=RUNTIME_ABI,
        state=state,
        model_state="trigger.STATE_RUNNING" if active else "trigger.STATE_IDLE",
        model_substate="trigger.STATE_EMPTY",
        last_block=0,
        output_enabled=output,
        ready_level=int(state == RuntimeState.WAITING_START),
        busy_level=int(state == RuntimeState.RUNNING),
        start_level=0,
        start_overrun=False,
        timer_overrun=False,
    )


def request(mode=StartMode.IMMEDIATE, **changes):
    return AcquisitionRequest(
        experiment_id="timed-resistor-candidate",
        start_mode=mode,
        program=GalvanostaticHold(
            **(
                dict(current_a=0.001, duration_s=0.4, sample_period_s=0.1, voltage_limit_v=0.2)
                | changes
            )
        ),
    )


def config():
    return Keithley2460Config(
        visa_resource="TCPIP0::192.0.2.1::5025::SOCKET",
        source_terminal="front",
        sense="remote",
        safety=SafetyConfig(
            voltage_min_v=-0.2,
            voltage_max_v=0.2,
            charge_current_max_a=0.001,
            discharge_current_max_a=0.001,
            power_abs_max_w=0.001,
            source_off_mode="normal",
        ),
        timing=TimingPolicy(
            command_timeout_s=1,
            external_start_timeout_s=2,
            shutdown_timeout_s=0.5,
            poll_period_s=0.01,
            required_abort_latency_s=1,
            required_cutoff_latency_s=1,
        ),
        buffer=BufferPolicy(capacity_ceiling_records=128, transfer_chunk_records=2),
    )


class FakeTransport:
    command_language = "TSP"
    identity = "KEITHLEY INSTRUMENTS,MODEL 2460,04686198,1.7.16a"
    present = "true"
    line_frequency = "50"
    unread_errors = "0"

    def __init__(self):
        self.config = VisaTransportConfig(
            resource_name="TCPIP0::192.0.2.1::5025::SOCKET",
            backend="@py",
            io_timeout_ms=1000,
            required_language="TSP",
        )
        self.calls = []
        self.build = module.timed_packaged_runtime().build

    def connect(self):
        self.calls.append("connect")

    def close(self):
        self.calls.append("close")

    def query(self, command):
        self.calls.append(command)
        if command == "*IDN?":
            return self.identity
        if command == "*LANG?":
            return self.command_language
        if command == "print(localnode.linefreq)":
            return self.line_frequency
        if command == "print(eventlog.getcount(eventlog.SEV_ERROR))":
            return self.unread_errors
        if command == "print(oe_m4_runtime_build)":
            return self.build
        if command == f"print({module.timed_packaged_runtime().script_name} ~= nil)":
            return self.present
        raise AssertionError(f"Unexpected transport query {command}")

    def write(self, command):
        raise AssertionError(f"Backend sent an unexpected direct mutation: {command}")

    def load_runtime(self, *args, **kwargs):
        raise AssertionError("Backend must never install a runtime")


class FakeController:
    def __init__(self):
        self.observation = status()
        self.capacity = 16
        self.records = ()
        self.cause = "unprepared"
        self.plan = None
        self.calls = []
        self.failures = {}
        self.read_started = None
        self.read_release = None

    def called(self, name):
        self.calls.append(name)
        error = self.failures.pop(name, None)
        if error is not None:
            raise error

    def status(self):
        self.called("status")
        return self.observation

    def buffer_info(self):
        self.called("buffer_info")
        count = len(self.records)
        return RuntimeBufferInfo(
            record_count=count,
            start_index=1 if count else None,
            end_index=count if count else None,
            capacity_records=self.capacity,
        )

    def terminal_cause(self):
        self.called("terminal_cause")
        return self.cause

    def prepare_timed_hold(self, plan):
        self.called("prepare")
        self.plan = plan
        self.capacity = plan.measurement_count
        self.observation = status(RuntimeState.PREPARED)
        return self.observation

    def arm(self):
        self.called("arm")
        external = self.plan.start_mode == StartMode.EXTERNAL_TRIGGER
        self.observation = status(
            RuntimeState.WAITING_START if external else RuntimeState.RUNNING,
            output=not external,
        )
        return self.observation

    def set_records(self, count):
        self.records = tuple(
            BufferedReading(
                sample_index=index,
                source_current_a=0.00100001,
                measured_voltage_v=0.1024,
                buffer_relative_time_s=index * 0.100012345,
                source_status=200,
                measurement_status=264 if index == 0 else 8,
            )
            for index in range(count)
        )

    def finish(self, count=None, *, state=RuntimeState.COMPLETE, cause="normal_completion"):
        self.set_records(self.plan.measurement_count if count is None else count)
        self.observation = status(state)
        self.cause = cause

    def read_buffer_chunk(self, *, offset, max_records):
        self.called("read")
        self.calls.append((offset, max_records))
        if self.read_started is not None:
            self.read_started.set()
            assert self.read_release.wait(2)
        records = self.records[offset : offset + max_records]
        return RuntimeRecordChunk(
            offset=offset,
            total_records=len(self.records),
            records=records,
            records_sha256=_buffered_records_sha256(records),
        )

    def abort(self):
        self.called("abort")
        self.cause = "operator_abort"
        self.observation = status(RuntimeState.ABORTED)
        return self.observation

    def recover(self):
        self.called("recover")
        self.cause = "unprepared"
        self.observation = status()
        return self.observation

    def discard_buffer(self, *, reason):
        self.called("discard")
        self.records = ()
        return self.buffer_info()


@pytest.fixture
def setup_backend(monkeypatch):
    transport = FakeTransport()
    controller = FakeController()
    monkeypatch.setattr(module, "TimedHoldRuntimeController", lambda *_: controller)
    backend = Keithley2460Backend(
        transport,
        config(),
        source_range_a=0.001,
        measurement_profile=K2460MeasurementProfile.NPLC_1,
        commissioning_resistor=True,
        expected_serial_number="04686198",
    )
    return backend, transport, controller


def prepare(backend, request_value=None):
    backend.connect()
    return backend.prepare(
        request() if request_value is None else request_value, acquisition_id="run1"
    )


def test_candidate_requires_explicit_resistor_opt_in():
    with pytest.raises(UnsupportedCapabilityError, match="commissioning_resistor"):
        Keithley2460Backend(FakeTransport(), config(), source_range_a=0.001)


def test_transport_resource_must_match_the_reviewed_configuration_before_connect():
    transport = FakeTransport()
    transport.config = replace(transport.config, resource_name="TCPIP0::192.0.2.2::5025::SOCKET")
    with pytest.raises(ValidationError, match="resource differs"):
        Keithley2460Backend(transport, config(), source_range_a=0.001, commissioning_resistor=True)
    assert not transport.calls


def test_unread_errors_prevent_prepare_and_arm_without_consuming_events(setup_backend):
    backend, transport, controller = setup_backend
    backend.connect()
    transport.unread_errors = "1"
    with pytest.raises(ElectrochemistryError, match="Unread instrument errors"):
        backend.prepare(request(), acquisition_id="run1")
    assert "prepare" not in controller.calls
    transport.unread_errors = "0"
    backend.prepare(request(), acquisition_id="run1")
    transport.unread_errors = "1"
    with pytest.raises(ElectrochemistryError, match="Unread instrument errors"):
        backend.kickoff()
    assert "arm" not in controller.calls
    assert not any("eventlog.next" in str(call) for call in transport.calls)


@pytest.mark.parametrize("frequency", ["49", "nan", "not-a-frequency"])
def test_connect_rejects_invalid_line_frequency_without_preparation(setup_backend, frequency):
    backend, transport, controller = setup_backend
    transport.line_frequency = frequency
    with pytest.raises(TransportProtocolError):
        backend.connect()
    assert "prepare" not in controller.calls


@pytest.mark.parametrize("mode", list(StartMode))
def test_candidate_runs_through_real_flyer_with_uncalibrated_event_documents(setup_backend, mode):
    backend, transport, controller = setup_backend
    device = Keithley2460Device(
        backend,
        name="ec",
        collection_chunk_records=2,
        supported_program_types=(GalvanostaticHold,),
    )
    documents = []
    engine = RunEngine({})
    engine.subscribe(lambda name, document: documents.append((name, document)))

    def plan():
        yield from bps.open_run()
        yield from bps.stage(device)
        yield from bps.prepare(device, request(mode), wait=True)
        yield from bps.kickoff(device, wait=True)
        if mode == StartMode.EXTERNAL_TRIGGER:
            assert device.state == DeviceState.WAITING_START
            assert device.output_enabled is False
        controller.finish()
        yield from bps.complete(device, wait=True)
        yield from bps.collect(device, return_payload=False)
        yield from bps.unstage(device)
        yield from bps.close_run()

    engine(plan())
    for name, document in documents:
        event_model.schema_validators[event_model.DocumentNames(name)].validate(document)
    descriptor = next(doc for name, doc in documents if name == "descriptor")
    configuration = descriptor["configuration"]["ec"]["data"]
    assert configuration["ec_relative_time_origin"] == "first_stored_reading"
    assert configuration["ec_actual_start_known"] == 0
    assert configuration["ec_instrument_timestamp_resolution"] == -1
    assert configuration["ec_current_origin"] == "source_readback"
    assert configuration["ec_target_hardware_ready"] == 0
    assert configuration["ec_target_timing_status"] == "uncommissioned"
    actual_plan = json.loads(configuration["ec_target_plan_json"])["fields"]
    assert actual_plan["duration_s"] == 0.4
    assert actual_plan["sample_period_s"] == 0.1
    assert configuration["ec_target_instrument_identity"] == transport.identity
    assert configuration["ec_target_line_frequency_hz"] == 50
    page = next(doc for name, doc in documents if name == "event_page")
    assert len(page["data"]) == 25
    assert page["data"]["ec_time_relative"] == [
        r.buffer_relative_time_s for r in controller.records
    ]
    assert page["data"]["ec_voltage_aperture_start"] == [-1] * 4
    assert page["data"]["ec_current_aperture_start"] == [-1] * 4
    assert page["data"]["ec_source_status"] == [200] * 4
    assert page["data"]["ec_measurement_status"] == [264, 8, 8, 8]
    assert device.state == DeviceState.COMPLETE and device.output_enabled is False
    assert "discard" not in controller.calls


def test_finite_timed_hold_lifecycle_retains_raw_pair_and_never_uploads(setup_backend):
    backend, transport, controller = setup_backend
    prepared = prepare(backend)
    assert not backend.hardware_ready
    assert prepared.request_sha256 == request_sha256(request())
    assert prepared.expected_records == prepared.capacity_records == 4
    assert prepared.completion_bound_s == pytest.approx(0.4 + 0.05 + 0.01 + 0.5)
    assert controller.plan.duration_s == 0.4 and controller.plan.sample_period_s == 0.1
    assert controller.plan.lower_cutoff_v == -0.2 and controller.plan.upper_cutoff_v == 0.2
    assert backend.inspect() == (DeviceState.PREPARED, False)
    backend.kickoff()
    assert backend.completion() is None
    controller.finish()
    assert backend.completion() == "normal_completion"
    assert "read" not in controller.calls  # transfer is deferred until collection
    first = backend.read_record_chunk(offset=0, max_records=2)
    assert first == backend.read_record_chunk(offset=0, max_records=2)
    assert controller.calls.count("read") == 2
    assert first.records[0].source_status == 200
    assert first.records[0].measurement_status == 264
    assert first.records[0].current_a == 0.00100001
    assert first.records[0].current_aperture is None
    assert backend.retained_schema().instrument_start_timestamp_s is None
    assert backend.retained_schema().instrument_timestamp_resolution_s is None
    assert backend.retained_metadata().waveform_complete
    with pytest.raises(RetainedDataError, match="disposition"):
        backend.prepare(request(), acquisition_id="run2")
    assert "discard" not in controller.calls
    assert all(
        "loadscript" not in command and "reset" not in command for command in transport.calls
    )


def test_external_kickoff_confirms_waiting_off_and_timeout_remains_failed_after_recovery(
    setup_backend,
):
    backend, _, controller = setup_backend
    prepared = prepare(backend, request(StartMode.EXTERNAL_TRIGGER))
    assert prepared.completion_bound_s > 2.4
    backend.kickoff()
    assert backend.inspect() == (DeviceState.WAITING_START, False)
    controller.finish(0, state=RuntimeState.START_TIMEOUT, cause="start_timeout")
    with pytest.raises(AcquisitionAborted, match="start_timeout"):
        backend.completion()
    assert backend.inspect() == (DeviceState.ERROR, False)
    backend.recover()
    assert backend.inspect() == (DeviceState.IDLE, False)
    with pytest.raises(AcquisitionAborted, match="start_timeout"):
        backend.completion()
    metadata = backend.retained_metadata()
    assert metadata.retained_records == 0 and not metadata.waveform_complete
    assert metadata.terminal_cause == "start_timeout"


def test_abort_keeps_partial_records_and_exact_disposition_is_required(setup_backend):
    backend, _, controller = setup_backend
    prepare(backend)
    backend.kickoff()
    controller.set_records(2)
    backend.abort(reason="operator stopped")
    backend.abort(reason="repeat request")
    assert controller.calls.count("abort") == 1
    with pytest.raises(AcquisitionAborted, match="operator stopped"):
        backend.completion()
    backend.recover()
    metadata = backend.retained_metadata()
    assert metadata.retained_records == 2 and not metadata.waveform_complete
    assert metadata.terminal_cause == "operator stopped"
    with pytest.raises(RetainedDataError, match="exact owned"):
        backend.discard_retained_data(acquisition_id="different", reason="reviewed")
    backend.discard_retained_data(acquisition_id="run1", reason="archived/reviewed")
    assert controller.records == ()
    assert backend.prepare(request(), acquisition_id="run2").expected_records == 4


@pytest.mark.parametrize("cause", ["cadence_overrun", "measurement_deadline", "voltage_cutoff"])
def test_runtime_fault_is_not_normal_completion_and_retained_prefix_survives(setup_backend, cause):
    backend, _, controller = setup_backend
    prepare(backend)
    backend.kickoff()
    controller.finish(1, state=RuntimeState.ERROR, cause=cause)
    with pytest.raises(AcquisitionAborted, match=cause):
        backend.completion()
    backend.recover()
    assert backend.retained_metadata().terminal_cause == cause
    assert backend.read_record_chunk(offset=0, max_records=2).records[0].source_status == 200


def test_uncertain_arm_is_never_retried_and_requires_explicit_safe_recovery(setup_backend):
    backend, _, controller = setup_backend
    prepare(backend)
    controller.failures["arm"] = TransportTimeoutError("lost arm reply")
    with pytest.raises(TransportTimeoutError):
        backend.kickoff()
    assert backend.inspect() == (DeviceState.ERROR, None)
    with pytest.raises(ShutdownUnconfirmed):
        backend.prepare(request(), acquisition_id="run2")
    with pytest.raises(ShutdownUnconfirmed):
        backend.retained_metadata()
    with pytest.raises(ShutdownUnconfirmed):
        backend.kickoff()
    assert controller.calls.count("arm") == 1
    controller.observation = status(RuntimeState.RUNNING, output=True)
    with pytest.raises(ShutdownUnconfirmed):
        backend.recover()
    assert "recover" not in controller.calls
    controller.finish(1, state=RuntimeState.ABORTED, cause="operator_abort")
    backend.recover()
    with pytest.raises(AcquisitionAborted, match="transport_failure"):
        backend.completion()
    assert backend.retained_metadata().retained_records == 1


def test_transfer_failure_requires_recovery_without_relabeling_completed_acquisition(setup_backend):
    backend, _, controller = setup_backend
    prepare(backend)
    backend.kickoff()
    controller.finish()
    backend.completion()
    controller.failures["read"] = TransportTimeoutError("lost chunk reply")
    with pytest.raises(TransportTimeoutError):
        backend.retained_metadata()
    with pytest.raises(ShutdownUnconfirmed):
        backend.retained_metadata()
    assert controller.calls.count("read") == 1
    backend.recover()
    assert backend.completion() == "normal_completion"
    assert backend.retained_metadata().waveform_complete


@pytest.mark.parametrize(
    "attribute,value",
    [
        ("identity", "KEITHLEY INSTRUMENTS,MODEL 2450,04686198,1.7.16a"),
        ("identity", "KEITHLEY INSTRUMENTS,MODEL 2460,other,1.7.16a"),
        ("identity", "KEITHLEY INSTRUMENTS,MODEL 2460,04686198,1.7.15"),
        ("command_language", "SCPI"),
        ("build", "m4-finite-current-hold-v14"),
        ("present", "false"),
    ],
)
def test_connection_rejects_wrong_target_without_mutation(setup_backend, attribute, value):
    backend, transport, controller = setup_backend
    setattr(transport, attribute, value)
    with pytest.raises(IncompatibleInstrumentError):
        backend.connect()
    assert not any(name in controller.calls for name in ("prepare", "arm", "recover", "discard"))


def test_unknown_old_records_cannot_be_adopted_overwritten_or_discarded(setup_backend):
    backend, _, controller = setup_backend
    controller.set_records(1)
    backend.connect()
    with pytest.raises(RetainedDataError, match="Unknown retained"):
        backend.prepare(request(), acquisition_id="run1")
    with pytest.raises(RetainedDataError, match="adopted"):
        backend.retained_metadata()
    with pytest.raises(RetainedDataError, match="exact owned"):
        backend.discard_retained_data(acquisition_id="run1", reason="unknown")
    assert "prepare" not in controller.calls and "discard" not in controller.calls


def test_preparation_rejects_unsupported_program_and_unsafe_or_unbounded_cadence(setup_backend):
    backend, _, controller = setup_backend
    backend.connect()
    voltage = AcquisitionRequest(
        experiment_id="unsupported",
        program=PotentiostaticHold(
            voltage=0.1,
            duration_s=1,
            sample_period_s=0.1,
            current_limit_a=0.001,
        ),
    )
    with pytest.raises(UnsupportedCapabilityError, match="GalvanostaticHold"):
        backend.prepare(voltage, acquisition_id="run1")
    for changes in ({"sample_period_s": 0.01}, {"duration_s": 0.45}, {"current_a": 0.002}):
        with pytest.raises(ValidationError):
            backend.prepare(request(**changes), acquisition_id="run1")
    assert "prepare" not in controller.calls


def test_explicit_cutoffs_are_intersected_with_configured_envelope(setup_backend):
    backend, _, controller = setup_backend
    prepare(backend, request(lower_cutoff_v=-0.3, upper_cutoff_v=0.15))
    assert controller.plan.lower_cutoff_v == -0.2
    assert controller.plan.upper_cutoff_v == 0.15


def test_cached_projection_refuses_stale_or_changed_extent(setup_backend):
    backend, _, controller = setup_backend
    prepare(backend)
    backend.kickoff()
    controller.finish()
    assert backend.retained_metadata().retained_records == 4
    controller.set_records(3)
    with pytest.raises(RetainedDataError, match="extent changed"):
        backend.retained_metadata()
    assert "discard" not in controller.calls


def test_normal_completion_cannot_claim_missing_records(setup_backend):
    backend, _, controller = setup_backend
    prepare(backend)
    backend.kickoff()
    controller.finish(3)
    with pytest.raises(RetainedDataError, match="unexpected retained extent"):
        backend.completion()
    with pytest.raises(AcquisitionAborted, match="incomplete"):
        backend.completion()
    assert not backend.retained_metadata().waveform_complete


def test_export_is_checksummed_exclusive_and_never_discards(setup_backend, tmp_path: Path):
    backend, _, controller = setup_backend
    prepare(backend)
    backend.kickoff()
    controller.finish(2, state=RuntimeState.ERROR, cause="voltage_cutoff")
    destination = tmp_path / "retained.json"
    digest = backend.export_retained_data(str(destination))
    envelope = json.loads(destination.read_text())
    assert digest == envelope["sha256"] == sha256_json(canonical_json(envelope["payload"]))
    assert envelope["payload"]["retained"]["fields"]["terminal_cause"] == "voltage_cutoff"
    assert envelope["payload"]["config"]["fields"]["sense"] == "remote"
    with pytest.raises(FileExistsError):
        backend.export_retained_data(str(destination))
    assert len(controller.records) == 2 and "discard" not in controller.calls


def test_connection_and_close_require_stopped_output_off(setup_backend):
    backend, _, controller = setup_backend
    controller.observation = status(RuntimeState.RUNNING, output=True)
    with pytest.raises(ShutdownUnconfirmed):
        backend.connect()
    controller.observation = status()
    prepare(backend)
    backend.kickoff()
    with pytest.raises(ShutdownUnconfirmed):
        backend.close()


def test_whole_transfer_serializes_against_abort(setup_backend):
    backend, _, controller = setup_backend
    prepare(backend)
    backend.kickoff()
    controller.finish()
    controller.read_started, controller.read_release = threading.Event(), threading.Event()
    outcomes = []

    def collect():
        outcomes.append(backend.retained_metadata())

    collection = threading.Thread(target=collect)
    collection.start()
    assert controller.read_started.wait(2)
    aborted = threading.Event()

    def abort():
        backend.abort()
        aborted.set()

    shutdown = threading.Thread(target=abort)
    shutdown.start()
    assert not aborted.wait(0.02)
    controller.read_release.set()
    collection.join(2)
    shutdown.join(2)
    assert len(outcomes) == 1 and aborted.is_set()
    assert "abort" not in controller.calls  # already terminal completion


def test_overrun_flag_cannot_become_success_after_safe_shutdown(setup_backend):
    backend, _, controller = setup_backend
    prepare(backend)
    backend.kickoff()
    controller.finish()
    controller.observation = replace(controller.observation, timer_overrun=True)
    with pytest.raises(AcquisitionAborted, match="trigger_overrun"):
        backend.completion()
    assert backend.inspect() == (DeviceState.ERROR, False)


def test_idle_edges_do_not_relabel_an_already_observed_clean_completion(setup_backend):
    backend, _, controller = setup_backend
    prepare(backend, request(StartMode.EXTERNAL_TRIGGER))
    backend.kickoff()
    controller.finish()
    assert backend.completion() == "normal_completion"
    controller.observation = replace(controller.observation, start_overrun=True)
    assert backend.completion() == "normal_completion"
    assert backend.retained_metadata().waveform_complete
    assert backend.inspect() == (DeviceState.COMPLETE, False)


def test_failed_model_output_on_gets_one_cleanup_abort_and_preserves_failure(setup_backend):
    backend, _, controller = setup_backend
    prepare(backend)
    backend.kickoff()
    controller.finish(1, state=RuntimeState.ERROR, cause="runtime_error")
    controller.observation = replace(
        controller.observation, output_enabled=True, model_state="trigger.STATE_FAILED"
    )
    assert backend.inspect() == (DeviceState.ERROR, True)
    assert "abort" not in controller.calls  # inspection has no hardware mutation
    with pytest.raises(AcquisitionAborted, match="runtime_error"):
        backend.completion()
    assert backend.inspect() == (DeviceState.ABORTED, False)
    with pytest.raises(AcquisitionAborted, match="runtime_error"):
        backend.completion()
    assert controller.calls.count("abort") == 1
    assert backend.retained_metadata().retained_records == 1
    assert backend.retained_metadata().terminal_cause == "runtime_error"
    assert not backend.retained_metadata().waveform_complete


def test_immediate_model_failure_during_kickoff_shuts_down_and_retains_prefix(setup_backend):
    backend, _, controller = setup_backend
    prepare(backend)

    def failed_arm():
        controller.called("arm")
        controller.finish(1, state=RuntimeState.ERROR, cause="runtime_error")
        controller.observation = replace(
            controller.observation, output_enabled=True, model_state="trigger.STATE_FAILED"
        )
        return controller.observation

    controller.arm = failed_arm
    with pytest.raises(AcquisitionAborted, match="runtime_error"):
        backend.kickoff()
    assert controller.calls.count("abort") == 1
    assert backend.inspect() == (DeviceState.ABORTED, False)
    assert backend.retained_metadata().retained_records == 1
    with pytest.raises(AcquisitionAborted, match="runtime_error"):
        backend.completion()


def test_uncertain_cleanup_abort_is_not_retried_and_requires_safe_recovery(setup_backend):
    backend, _, controller = setup_backend
    prepare(backend)
    backend.kickoff()
    controller.finish(1, state=RuntimeState.ERROR, cause="measurement_deadline")
    controller.observation = replace(controller.observation, output_enabled=True)
    controller.failures["abort"] = TransportTimeoutError("lost abort reply")
    with pytest.raises(TransportTimeoutError):
        backend.completion()
    assert backend.inspect() == (DeviceState.ERROR, None)
    with pytest.raises(ShutdownUnconfirmed):
        backend.completion()
    with pytest.raises(ShutdownUnconfirmed):
        backend.abort()
    assert controller.calls.count("abort") == 1
    with pytest.raises(ShutdownUnconfirmed):
        backend.recover()
    controller.observation = status(RuntimeState.ABORTED)
    backend.recover()
    with pytest.raises(AcquisitionAborted, match="measurement_deadline"):
        backend.completion()
    assert backend.retained_metadata().retained_records == 1


def test_uncertain_arm_without_execution_can_cancel_prepared_off_model_by_recovery(setup_backend):
    backend, _, controller = setup_backend
    prepare(backend)
    controller.failures["arm"] = TransportTimeoutError("arm was not delivered")
    with pytest.raises(TransportTimeoutError):
        backend.kickoff()
    assert controller.observation.state == RuntimeState.PREPARED
    backend.recover()
    with pytest.raises(AcquisitionAborted, match="transport_failure"):
        backend.completion()
    assert backend.retained_metadata().retained_records == 0


def test_preparation_capacity_mismatch_reports_unknown_until_explicit_recovery(setup_backend):
    backend, _, controller = setup_backend
    original = controller.prepare_timed_hold

    def prepare_bad_capacity(plan):
        result = original(plan)
        controller.capacity = 129
        return result

    controller.prepare_timed_hold = prepare_bad_capacity
    backend.connect()
    with pytest.raises(RetainedDataError, match="capacity ceiling"):
        backend.prepare(request(), acquisition_id="run1")
    assert backend.inspect() == (DeviceState.ERROR, None)
    assert "discard" not in controller.calls


def test_higher_signal_profile_binds_five_ma_one_v_schedule_and_cached_metadata(setup_backend):
    _, transport, controller = setup_backend
    settings = replace(
        config(),
        safety=replace(
            config().safety,
            voltage_min_v=-1.0,
            voltage_max_v=1.0,
            charge_current_max_a=0.005,
            discharge_current_max_a=0.005,
            power_abs_max_w=0.005,
        ),
    )
    backend = Keithley2460Backend(
        transport, settings, source_range_a=0.01, commissioning_resistor=True
    )
    assert backend.configuration_metadata()["request_json"] == "unprepared"
    prepared = prepare(
        backend,
        request(current_a=0.005, voltage_limit_v=1, duration_s=1, upper_cutoff_v=0.8),
    )
    assert prepared.expected_records == 10
    calls = len(controller.calls), len(transport.calls)
    metadata = backend.configuration_metadata()
    assert calls == (len(controller.calls), len(transport.calls))
    assert metadata["hardware_ready"] == 0 and metadata["timing_status"] == "uncommissioned"
    assert metadata["measurement_profile"] == "k2460_2v_1nplc_v1"
    assert len(metadata["runtime_sha256"]) == 64
    plan = json.loads(metadata["plan_json"])["fields"]
    assert plan["duration_s"] == 1 and plan["sample_period_s"] == 0.1
    assert plan["current_a"] == 0.005 and plan["voltage_limit_v"] == 1
    assert plan["lower_cutoff_v"] == -1 and plan["upper_cutoff_v"] == 0.8
    assert plan["measurement_profile"]["value"] == "k2460_2v_1nplc_v1"
    assert "runtime" not in json.loads(metadata["config_json"])["fields"]
