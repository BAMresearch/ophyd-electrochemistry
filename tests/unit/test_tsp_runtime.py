"""Offline checks for the packaged, narrow M4 TSP proof runtime."""

from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import replace

import pytest

from ophyd_electrochemistry.acquisition import StartMode
from ophyd_electrochemistry.exceptions import (
    IncompatibleInstrumentError,
    TransportProtocolError,
    ValidationError,
)
from ophyd_electrochemistry.keithley.k2460 import (
    K2460_BUFFER_SCHEMA,
    CurrentHoldAcquisitionProof,
    CurrentHoldProof,
    Keithley2460Config,
    M4RuntimeController,
    RuntimeState,
    SafetyConfig,
    TimingPolicy,
    packaged_runtime,
    parse_buffered_readings,
    parse_runtime_buffer_info,
    parse_runtime_status,
)


def status_line(
    state: str,
    *,
    block: int = 0,
    output: int | str = 0,
    ready: int = 0,
    busy: int = 0,
    start: int = -1,
) -> str:
    return "\t".join(
        (
            "oe-k2460-m4-hold-v1",
            state,
            "trigger.STATE_IDLE",
            "trigger.STATE_EMPTY",
            str(block),
            str(output),
            str(ready),
            str(busy),
            str(start),
            "false",
            "false",
        )
    )


class FakeRuntimeTransport:
    def __init__(self, *replies: str, language: str = "TSP") -> None:
        self.command_language = language
        self.replies = deque(replies)
        self.writes: list[str] = []
        self.load_arguments: tuple[str, str, str] | None = None

    def write(self, command: str) -> None:
        self.writes.append(command)

    def query(self, command: str) -> str:
        self.writes.append(command)
        return self.replies.popleft()

    def load_runtime(self, source: str, *, abi: str, sha256: str) -> None:
        self.load_arguments = source, abi, sha256


def runtime_config(**changes: object) -> Keithley2460Config:
    values: dict[str, object] = {
        "visa_resource": "TCPIP0::192.0.2.1::5025::SOCKET",
        "source_terminal": "front",
        "sense": "remote",
        "safety": SafetyConfig(
            voltage_min_v=-0.2,
            voltage_max_v=0.2,
            charge_current_max_a=0.001,
            discharge_current_max_a=0.001,
            power_abs_max_w=0.001,
            source_off_mode="normal",
        ),
        "timing": TimingPolicy(
            command_timeout_s=2,
            external_start_timeout_s=2,
            shutdown_timeout_s=2,
            poll_period_s=0.01,
            required_abort_latency_s=1,
            required_cutoff_latency_s=1,
        ),
    }
    values.update(changes)
    return Keithley2460Config(**values)  # type: ignore[arg-type]


def hold(**changes: object) -> CurrentHoldProof:
    values: dict[str, object] = {
        "current_a": 0.001,
        "source_range_a": 0.001,
        "voltage_limit_v": 0.2,
        "duration_s": 0.25,
    }
    values.update(changes)
    return CurrentHoldProof(**values)  # type: ignore[arg-type]


def test_packaged_runtime_has_stable_identity_and_only_local_triggerflow_timing():
    artifact = packaged_runtime()

    assert artifact.abi == "oe-k2460-m4-hold-v1"
    assert artifact.build == "m4-finite-current-hold-v5"
    assert artifact.sha256 == hashlib.sha256(artifact.source.encode("ascii")).hexdigest()
    assert artifact.script_name == f"oe_m4_{artifact.sha256[:24]}"
    assert len(artifact.script_name) == 30
    assert "CONTRACT TEMPLATE ONLY" not in artifact.source
    assert 'trigger.model.load("Empty")' in artifact.source
    assert "trigger.BLOCK_SOURCE_OUTPUT, smu.ON" in artifact.source
    assert "trigger.BLOCK_SOURCE_OUTPUT, smu.OFF" in artifact.source
    assert "trigger.EVENT_TIMER1" in artifact.source
    assert "trigger.model.abort()" in artifact.source
    assert "delay(" not in artifact.source
    assert "smu.source.offmode = smu.OFFMODE_NORMAL" in artifact.source
    assert "math.huge" not in artifact.source
    assert "oe_m4_ready_level" in artifact.source
    assert "oe_m4_busy_level" in artifact.source
    assert "oe_m4_start_level" in artifact.source
    assert "oe_m4_track_flags(true, false)" in artifact.source
    assert "oe_m4_track_flags(false, true)" in artifact.source
    assert "digio.MODE_DIGITAL_IN" in artifact.source
    assert "expected_inactive = digio.STATE_HIGH" in artifact.source
    assert "buffer.make(oe_m5_buffer_capacity, buffer.STYLE_STANDARD)" in artifact.source
    assert "trigger.BLOCK_BUFFER_CLEAR, oe_m5_buffer" in artifact.source
    assert "trigger.BLOCK_MEASURE_DIGITIZE, oe_m5_buffer, measurement_count" in artifact.source
    assert "oe_m5_buffer.sourcevalues" in artifact.source
    assert "oe_m5_buffer.relativetimestamps" in artifact.source
    assert "oe_m5_buffer.sourcestatuses" in artifact.source
    assert "oe_m5_buffer.statuses" in artifact.source
    assert "retained M5 proof data must be explicitly discarded" in artifact.source
    assert "oe_m4_start_level = digio.line[oe_m4_start_line].state" in artifact.source
    assert "start_state = digio.line[oe_m4_start_line].state" not in artifact.source
    assert "digio.line[oe_m4_ready_line].state," not in artifact.source
    assert "digio.line[oe_m4_busy_line].state," not in artifact.source
    assert not artifact.source.rstrip().endswith("oe_m4_initialize()")
    assert "\n\n" not in artifact.source


@pytest.mark.parametrize(
    "changes",
    [
        {"current_a": 0.0101, "source_range_a": 0.0101},
        {"source_range_a": 0.0001},
        {"voltage_limit_v": 0.009},
        {"voltage_limit_v": 2.01},
        {"duration_s": 0.0009},
        {"duration_s": 60.1},
        {"start_mode": "immediate"},
    ],
)
def test_current_hold_proof_rejects_values_outside_hard_runtime_envelope(changes):
    with pytest.raises(ValidationError):
        hold(**changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"current_a": 0.0101, "source_range_a": 0.0101},
        {"source_range_a": 0.0001},
        {"voltage_limit_v": 0.009},
        {"voltage_limit_v": 2.01},
        {"measurement_count": 0},
        {"measurement_count": 9},
        {"measurement_count": True},
    ],
)
def test_current_hold_acquisition_rejects_values_outside_proof_envelope(changes):
    values: dict[str, object] = {
        "current_a": 0.001,
        "source_range_a": 0.001,
        "voltage_limit_v": 0.2,
        "measurement_count": 3,
    }
    with pytest.raises(ValidationError):
        CurrentHoldAcquisitionProof(**(values | changes))  # type: ignore[arg-type]


def test_runtime_status_parser_is_strict_and_typed():
    parsed = parse_runtime_status(status_line("waiting_start", block=5, ready=1, start=0))
    assert parsed.state == RuntimeState.WAITING_START
    assert parsed.last_block == 5
    assert parsed.ready_level == 1
    assert parsed.output_enabled is False
    assert parse_runtime_status(status_line("idle", output="smu.OFF")).output_enabled is False
    assert parse_runtime_status(status_line("running", output="smu.ON")).output_enabled is True
    symbolic = status_line("waiting_start", ready=1, start=0).replace(
        "\t1\t0\t0\t", "\tdigio.STATE_HIGH\tdigio.STATE_LOW\tdigio.STATE_LOW\t"
    )
    assert parse_runtime_status(symbolic).ready_level == 1
    assert parse_runtime_status(symbolic).busy_level == 0
    assert parse_runtime_status(symbolic).start_level == 0
    unknown_flags = parse_runtime_status(status_line("idle", ready=-1, busy=-1))
    assert unknown_flags.ready_level == unknown_flags.busy_level == -1

    with pytest.raises(TransportProtocolError, match="11"):
        parse_runtime_status("too\tshort")
    with pytest.raises(IncompatibleInstrumentError, match="ABI"):
        parse_runtime_status(status_line("idle").replace("oe-k2460-m4-hold-v1", "wrong"))
    with pytest.raises(TransportProtocolError, match="output"):
        parse_runtime_status(status_line("idle").replace("\t0\t0\t0\t-1", "\t2\t0\t0\t-1"))


def test_runtime_buffer_parsers_are_strict_and_keep_raw_timestamp_meaning():
    info = parse_runtime_buffer_info("3\t1\t3\t16")
    assert info.record_count == 3 and info.start_index == 1 and info.end_index == 3
    assert parse_runtime_buffer_info("0\t0\t0\t16").start_index is None
    records = parse_buffered_readings(
        "0.000999998,0.10249,0,136,8, 0.000999997,0.10248,0.0825,136,8",
        offset=2,
        expected_count=2,
    )
    assert [record.sample_index for record in records] == [2, 3]
    assert records[0].source_current_a == pytest.approx(0.000999998)
    assert records[1].buffer_relative_time_s == pytest.approx(0.0825)
    assert records[0].source_status == 136 and records[0].measurement_status == 8
    with pytest.raises(TransportProtocolError, match="4 tab"):
        parse_runtime_buffer_info("3\t1\t3")
    with pytest.raises(TransportProtocolError, match="10"):
        parse_buffered_readings("1,2,3", offset=0, expected_count=2)


def test_controller_installs_only_the_packaged_artifact_and_requires_tsp():
    transport = FakeRuntimeTransport()
    controller = M4RuntimeController(transport, runtime_config())
    artifact = controller.install()
    assert transport.load_arguments == (artifact.source, artifact.abi, artifact.sha256)

    with pytest.raises(IncompatibleInstrumentError, match="TSP"):
        M4RuntimeController(
            FakeRuntimeTransport(language="SCPI"),  # type: ignore[arg-type]
            runtime_config(),
        ).install()


def test_exact_known_v4_can_be_replaced_without_a_reboot():
    transport = FakeRuntimeTransport(
        status_line("idle"),
        "0",
        "oe-k2460-m4-hold-v1",
        "m4-finite-current-hold-v4",
        "false",
        "true",
        "true",
        "smu.OFF",
        "0",
    )
    controller = M4RuntimeController(transport, runtime_config())

    artifact = controller.replace_known_v4_and_install()

    assert artifact == packaged_runtime()
    assert transport.load_arguments == (artifact.source, artifact.abi, artifact.sha256)
    assert 'script.delete("oe_m4_ce741e17ffcdd7f55d489c7b")' in transport.writes
    delete_index = transport.writes.index('script.delete("oe_m4_ce741e17ffcdd7f55d489c7b")')
    assert transport.writes[delete_index + 1] == ("print(oe_m4_ce741e17ffcdd7f55d489c7b == nil)")
    assert "oe_m4_runtime_abi = nil" in transport.writes
    assert "oe_m4_initialize = nil" in transport.writes


@pytest.mark.parametrize(
    ("replies", "match"),
    [
        ((status_line("running", output=1),), "output-OFF IDLE"),
        ((status_line("idle"), "0.001"), "zero programmed"),
        (
            (status_line("idle"), "0", "oe-k2460-m4-hold-v1", "unexpected-build"),
            "exact replaceable",
        ),
        (
            (
                status_line("idle"),
                "0",
                "oe-k2460-m4-hold-v1",
                "m4-finite-current-hold-v4",
                "true",
            ),
            "not present",
        ),
    ],
)
def test_known_v4_replacement_refuses_any_precondition_mismatch(replies, match):
    transport = FakeRuntimeTransport(*replies)
    with pytest.raises(IncompatibleInstrumentError, match=match):
        M4RuntimeController(transport, runtime_config()).replace_known_v4_and_install()
    assert not any(command.startswith("script.delete") for command in transport.writes)


def test_prepare_serializes_validated_parameters_and_confirms_output_off():
    transport = FakeRuntimeTransport(status_line("prepared"))
    controller = M4RuntimeController(transport, runtime_config())

    status = controller.prepare_current_hold(hold(start_mode=StartMode.EXTERNAL_TRIGGER))

    assert status.state == RuntimeState.PREPARED
    assert transport.writes[0] == (
        "oe_m4_prepare_current_hold(0.001,0.001,0.20000000000000001,0.25,1,2,0.01,1,1,1,2,3,1,1,1)"
    )
    assert transport.writes[1] == "print(oe_m4_status())"


def test_prepare_acquisition_serializes_bounded_fixed_measurement_and_empty_buffer():
    transport = FakeRuntimeTransport(status_line("prepared"), "0\t0\t0\t16")
    controller = M4RuntimeController(transport, runtime_config())
    acquisition = CurrentHoldAcquisitionProof(
        current_a=0.001,
        source_range_a=0.001,
        voltage_limit_v=0.2,
        measurement_count=3,
    )

    status = controller.prepare_current_hold_acquisition(acquisition)

    assert status.state == RuntimeState.PREPARED
    assert transport.writes == [
        "oe_m5_prepare_current_hold_acquisition(0.001,0.001,0.20000000000000001,3,2,0.01,1,1,1,2,3,1,1,1)",
        "print(oe_m4_status())",
        "print(oe_m5_buffer_status())",
    ]


def test_offset_buffer_chunks_are_retryable_and_discard_is_explicit():
    data = "0.001,0.1025,0,136,8,0.001,0.1026,0.0825,136,8"
    transport = FakeRuntimeTransport(
        status_line("complete", block=7),
        "3\t1\t3\t16",
        data,
        status_line("complete", block=7),
        "3\t1\t3\t16",
        data,
        status_line("complete", block=7),
        "0\t0\t0\t16",
    )
    controller = M4RuntimeController(transport, runtime_config())

    first = controller.read_buffer_chunk(offset=0, max_records=2)
    retry = controller.read_buffer_chunk(offset=0, max_records=2)
    assert first == retry and first.schema == K2460_BUFFER_SCHEMA
    assert first.next_offset == 2 and not first.final
    assert len(first.records_sha256) == 64
    assert controller.discard_buffer(reason="archived test record").record_count == 0
    assert transport.writes[-3:] == [
        "print(oe_m4_status())",
        "oe_m5_discard_buffer()",
        "print(oe_m5_buffer_status())",
    ]


def test_prepare_rejects_config_limits_off_mode_and_event_poll_before_write():
    cases = (
        runtime_config(safety=replace(runtime_config().safety, charge_current_max_a=0.0005)),
        runtime_config(
            safety=replace(runtime_config().safety, voltage_min_v=-0.1, voltage_max_v=0.1)
        ),
        runtime_config(safety=replace(runtime_config().safety, power_abs_max_w=0.0001)),
        runtime_config(safety=replace(runtime_config().safety, source_off_mode="highz")),
        runtime_config(timing=replace(runtime_config().timing, poll_period_s=0.2)),
    )
    for config in cases:
        transport = FakeRuntimeTransport()
        with pytest.raises(ValidationError):
            M4RuntimeController(transport, config).prepare_current_hold(hold())
        assert transport.writes == []


def test_arm_abort_force_safe_and_recover_require_confirmed_runtime_states():
    transport = FakeRuntimeTransport(
        status_line("running", block=3, output=1, busy=1),
        status_line("aborted", block=3),
        status_line("aborted", block=3),
        status_line("idle", block=3),
    )
    controller = M4RuntimeController(transport, runtime_config())

    assert controller.arm().state == RuntimeState.RUNNING
    assert controller.abort().state == RuntimeState.ABORTED
    assert controller.force_safe().output_enabled is False
    assert controller.recover().state == RuntimeState.IDLE
    assert transport.writes == [
        "oe_m4_arm()",
        "print(oe_m4_status())",
        "oe_m4_abort()",
        "print(oe_m4_status())",
        "oe_m4_force_safe()",
        "print(oe_m4_status())",
        "oe_m4_recover()",
        "print(oe_m4_status())",
    ]
