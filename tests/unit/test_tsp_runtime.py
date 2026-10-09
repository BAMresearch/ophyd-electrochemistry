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
    CurrentHoldProof,
    Keithley2460Config,
    M4RuntimeController,
    RuntimeState,
    SafetyConfig,
    TimingPolicy,
    packaged_runtime,
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
    assert artifact.build == "m4-finite-current-hold-v2"
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
    assert "oe_m4_track_flags(true, false)" in artifact.source
    assert "oe_m4_track_flags(false, true)" in artifact.source
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


def test_prepare_serializes_validated_parameters_and_confirms_output_off():
    transport = FakeRuntimeTransport(status_line("prepared"))
    controller = M4RuntimeController(transport, runtime_config())

    status = controller.prepare_current_hold(hold(start_mode=StartMode.EXTERNAL_TRIGGER))

    assert status.state == RuntimeState.PREPARED
    assert transport.writes[0] == (
        "oe_m4_prepare_current_hold(0.001,0.001,0.20000000000000001,0.25,1,2,0.01,1,1,1,2,3,1,1,1)"
    )
    assert transport.writes[1] == "print(oe_m4_status())"


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
