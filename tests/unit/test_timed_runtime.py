"""Offline candidate scheduling, artifact identity and guarded adapter tests."""

import hashlib
import re
from collections import deque
from dataclasses import replace
from pathlib import Path

import pytest

from ophyd_electrochemistry.acquisition import StartMode
from ophyd_electrochemistry.exceptions import (
    IncompatibleInstrumentError,
    TransportProtocolError,
    ValidationError,
)
from ophyd_electrochemistry.keithley.k2460.config import (
    BufferPolicy,
    DigitalIOConfig,
    Keithley2460Config,
    SafetyConfig,
    TimingPolicy,
)
from ophyd_electrochemistry.keithley.k2460.measurement_profiles import K2460MeasurementProfile
from ophyd_electrochemistry.keithley.k2460.runtime import RuntimeState, packaged_runtime
from ophyd_electrochemistry.keithley.k2460.timed_runtime import (
    TIMED_RUNTIME_BUILD,
    TimedHoldPlan,
    TimedHoldRuntimeController,
    timed_packaged_runtime,
)


class FakeTransport:
    command_language = "TSP"

    def __init__(self, *replies: str) -> None:
        self.replies = deque(replies)
        self.commands: list[str] = []
        self.loaded: tuple[str, str, str] | None = None

    def query(self, command: str) -> str:
        self.commands.append(command)
        return self.replies.popleft()

    def write(self, command: str) -> None:
        self.commands.append(command)

    def load_runtime(self, source: str, *, abi: str, sha256: str) -> None:
        self.loaded = source, abi, sha256


def config() -> Keithley2460Config:
    return Keithley2460Config(
        visa_resource="TCPIP0::192.0.2.1::5025::SOCKET",
        safety=SafetyConfig(
            voltage_min_v=-0.2,
            voltage_max_v=0.2,
            charge_current_max_a=0.01,
            discharge_current_max_a=0.01,
            power_abs_max_w=0.02,
            source_off_mode="normal",
        ),
        timing=TimingPolicy(
            command_timeout_s=1,
            external_start_timeout_s=2,
            shutdown_timeout_s=1,
            poll_period_s=0.01,
            required_abort_latency_s=1,
            required_cutoff_latency_s=1,
        ),
        source_terminal="front",
        sense="remote",
        buffer=BufferPolicy(capacity_ceiling_records=1000),
    )


def plan(**changes: object) -> TimedHoldPlan:
    values = dict(
        current_a=0.001,
        source_range_a=0.001,
        voltage_limit_v=0.2,
        duration_s=1,
        sample_period_s=0.1,
    )
    return TimedHoldPlan(**(values | changes))  # type: ignore[arg-type]


def status(state: str = "prepared", *, output: int = 0, model: str = "trigger.STATE_IDLE") -> str:
    return (
        f"oe-k2460-m4-hold-v1\t{state}\t{model}\ttrigger.STATE_EMPTY\t0\t{output}"
        "\t0\t0\t-1\tfalse\tfalse"
    )


def prepare_replies(
    *, external: bool = False, timed_readback: str = "1\t0.1\t10\t-0.2\t0.2\t0.05"
) -> tuple[str, ...]:
    return (
        TIMED_RUNTIME_BUILD,
        "false",
        status(),
        "0\t0\t0\t16",
        "1\t1\t0.2\t0\t0\t0\t0",
        timed_readback,
        "0.001\t0.001\t0.2",
        "|".join(f"{i}) NOP" for i in range(1, 40 if external else 30)),
    )


def test_timed_artifact_preserves_commissioned_v14_and_has_its_own_identity() -> None:
    base = packaged_runtime()
    artifact = timed_packaged_runtime()
    assert base.build == "m4-finite-current-hold-v14"
    assert base.sha256 == "c4c40686bf2016f5c55c820b5d0bd34f78915dd10e6a77823d0a9cad245c6e82"
    assert artifact.source.startswith(base.source)
    assert artifact.abi == base.abi
    assert artifact.build == TIMED_RUNTIME_BUILD
    assert artifact.sha256 == hashlib.sha256(artifact.source.encode("ascii")).hexdigest()
    assert artifact.sha256 != base.sha256
    assert artifact.script_name == f"oe_m4_{artifact.sha256[:24]}"
    assert artifact.source.endswith("\n")
    assert "\n\n" not in artifact.source
    assert "\r" not in artifact.source
    # The instrument uses Lua 5.0.2; forwarding with an ellipsis expression is
    # a Lua 5.1 feature. Keep this candidate free of vararg forwarding syntax.
    executable_lines = [line.split("--", 1)[0] for line in artifact.source.splitlines()]
    assert all("..." not in line for line in executable_lines)


def test_plan_uses_exact_nominal_duration_spacing_and_count() -> None:
    value = plan(duration_s=0.3, sample_period_s=0.1)
    assert value.measurement_count == 3
    assert value.measurement_budget_s == 0.05
    assert value.effective_voltage_cutoffs(config()) == (-0.2, 0.2)
    value.validate_config(config())


@pytest.mark.parametrize(
    "changes",
    [
        {"sample_period_s": 0.01},
        {"duration_s": 60.1},
        {"duration_s": 0.05},
        {"duration_s": 1.01},
        {"sample_period_s": 0.3},
        {"current_a": 0.002},
        {"source_range_a": 0.002},
        {"source_range_a": 0},
        {"voltage_limit_v": 0.3},
        {"voltage_limit_v": 0.001},
        {"current_a": float("nan")},
        {"duration_s": float("inf")},
        {"sample_period_s": True},
        {"start_mode": "immediate"},
        {"lower_cutoff_v": 0.1, "upper_cutoff_v": 0.1},
        {"measurement_profile": "k2460_0p2v_1nplc_v1"},
    ],
)
def test_invalid_plan_is_rejected_without_transport(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        plan(**changes)


def test_cutoffs_are_intersection_of_requested_configured_and_compliance_bounds() -> None:
    value = plan(voltage_limit_v=0.1, lower_cutoff_v=-1, upper_cutoff_v=0.08)
    assert value.effective_voltage_cutoffs(config()) == (-0.1, 0.08)
    value.validate_config(config())
    with pytest.raises(ValidationError, match="intersection"):
        plan(lower_cutoff_v=0.3).validate_config(config())


@pytest.mark.parametrize(
    "changes",
    [
        {"voltage_min_v": -0.1},
        {"voltage_min_v": 0.01},
        {"charge_current_max_a": 0.0005},
        {"power_abs_max_w": 0.00001},
        {"source_off_mode": "high_impedance"},
    ],
)
def test_config_safety_is_checked_before_any_transport(changes: dict[str, object]) -> None:
    original = config()
    changed = replace(original, safety=replace(original.safety, **changes))
    transport = FakeTransport()
    with pytest.raises(ValidationError):
        TimedHoldRuntimeController(transport, changed).prepare_timed_hold(plan())
    assert not transport.commands


def test_config_capacity_and_external_abort_remain_explicit_gates() -> None:
    original = config()
    for changed in (
        replace(
            original, buffer=BufferPolicy(capacity_ceiling_records=5, transfer_chunk_records=5)
        ),
        replace(original, io=DigitalIOConfig(external_abort_enabled=True)),
        replace(
            original, buffer=BufferPolicy(capacity_ceiling_records=10, transfer_chunk_records=2)
        ),
    ):
        with pytest.raises(ValidationError):
            plan().validate_config(changed)


def test_prepare_transfers_independent_timing_and_checks_profile_and_plan() -> None:
    transport = FakeTransport(*prepare_replies())
    result = TimedHoldRuntimeController(transport, config()).prepare_timed_hold(plan())
    assert result.state == RuntimeState.PREPARED
    assert not result.output_enabled
    assert transport.commands[2] == (
        "oe_timed_prepare_current_hold(0.001,0.001,0.20000000000000001,1,"
        "0.10000000000000001,10,-0.20000000000000001,0.20000000000000001,"
        "0,2,0.01,1,1,1,2,3,1,1,1,1)"
    )
    assert transport.commands[-4:-1] == [
        "print(oe_m5_measurement_profile_status())",
        "print(oe_timed_plan_status())",
        "print(smu.source.level, smu.source.range, smu.source.vlimit.level)",
    ]
    assert not transport.replies


def test_external_mode_is_transferred_without_arm_or_output_mutation() -> None:
    transport = FakeTransport(*prepare_replies(external=True))
    TimedHoldRuntimeController(transport, config()).prepare_timed_hold(
        plan(start_mode=StartMode.EXTERNAL_TRIGGER)
    )
    command = transport.commands[2]
    assert ",1,2,0.01,1,1,1,2,3,1,1,1,1)" in command
    assert "oe_m4_arm()" not in transport.commands
    assert all("smu.source.output =" not in command for command in transport.commands)


@pytest.mark.parametrize("build", ["nil", "m4-finite-current-hold-v14", "unrecognized-v15"])
def test_wrong_runtime_build_is_rejected_before_prepare(build: str) -> None:
    transport = FakeTransport(build)
    with pytest.raises(IncompatibleInstrumentError, match="packaged timed"):
        TimedHoldRuntimeController(transport, config()).prepare_timed_hold(plan())
    assert transport.commands == ["print(oe_m4_runtime_build)"]


def test_missing_exact_script_rejects_prepare() -> None:
    transport = FakeTransport(TIMED_RUNTIME_BUILD, "true")
    with pytest.raises(IncompatibleInstrumentError, match="digest"):
        TimedHoldRuntimeController(transport, config()).prepare_timed_hold(plan())
    assert not any(command.startswith("oe_timed_prepare") for command in transport.commands)


@pytest.mark.parametrize(
    "readback",
    ["2\t0.1\t10\t-0.2\t0.2\t0.05", "1\t0.2\t10\t-0.2\t0.2\t0.05", "nan", "1"],
)
def test_mismatched_plan_readback_fails_preparation(readback: str) -> None:
    transport = FakeTransport(*prepare_replies(timed_readback=readback))
    with pytest.raises(TransportProtocolError, match="readback"):
        TimedHoldRuntimeController(transport, config()).prepare_timed_hold(plan())
    assert "oe_m4_arm()" not in transport.commands


@pytest.mark.parametrize(
    "cause",
    [
        "normal_completion",
        "voltage_cutoff",
        "measurement_deadline",
        "cadence_overrun",
        "operator_abort",
    ],
)
def test_terminal_cause_is_preserved_as_a_distinct_observation(cause: str) -> None:
    transport = FakeTransport(cause)
    assert TimedHoldRuntimeController(transport, config()).terminal_cause() == cause


def test_unknown_terminal_cause_is_rejected() -> None:
    with pytest.raises(TransportProtocolError, match="cause"):
        TimedHoldRuntimeController(FakeTransport("success-ish"), config()).terminal_cause()


def test_known_error_on_arm_preserves_output_observation_for_cleanup() -> None:
    controller = TimedHoldRuntimeController(FakeTransport(status("error")), config())
    assert controller.arm().state == RuntimeState.ERROR
    observation = TimedHoldRuntimeController(
        FakeTransport(status("error", output=1)), config()
    ).arm()
    assert observation.state == RuntimeState.ERROR and observation.output_enabled


def test_install_does_not_inspect_undefined_status_when_globals_absent() -> None:
    transport = FakeTransport("true")
    result = TimedHoldRuntimeController(transport, config()).install()
    assert transport.loaded == (result.source, result.abi, result.sha256)
    assert transport.commands == ["print(oe_m4_runtime_abi == nil and oe_m4_initialize == nil)"]


def test_install_never_replaces_old_runtime_automatically() -> None:
    transport = FakeTransport("false", "m4-finite-current-hold-v14")
    with pytest.raises(IncompatibleInstrumentError, match="explicit guarded replacement"):
        TimedHoldRuntimeController(transport, config()).install()
    assert transport.loaded is None


def test_reinstall_rejects_retained_data_before_initializer() -> None:
    transport = FakeTransport("false", TIMED_RUNTIME_BUILD, status("idle"), "0", "1\t1\t1\t16")
    with pytest.raises(IncompatibleInstrumentError, match="retained"):
        TimedHoldRuntimeController(transport, config()).install()
    assert transport.loaded is None


@pytest.mark.parametrize(
    "observation",
    [status("prepared"), status("idle", output=1), status("idle", model="trigger.STATE_RUNNING")],
)
def test_reinstall_requires_stopped_idle_output_off(observation: str) -> None:
    transport = FakeTransport("false", TIMED_RUNTIME_BUILD, observation)
    with pytest.raises(IncompatibleInstrumentError, match="stopped"):
        TimedHoldRuntimeController(transport, config()).install()
    assert transport.loaded is None


def test_tsp_fault_paths_locally_turn_off_before_terminal_notification() -> None:
    source = timed_packaged_runtime().source[len(packaged_runtime().source) :]
    blocks = {
        int(match.group(1)): match.group(2)
        for match in re.finditer(
            r"trigger\.model\.setblock\(block\((\d+)\),\s*(.*?)\)\s*\n", source, re.S
        )
    }
    for off, notify in ((18, 20), (24, 26), (28, 30), (32, 34), (36, 38)):
        assert blocks[off] == "trigger.BLOCK_SOURCE_OUTPUT, smu.OFF"
        assert "trigger.BLOCK_DIGITAL_IO, oe_m4_pattern(false, false)" in blocks[off + 1]
        assert blocks[notify].startswith("trigger.BLOCK_NOTIFY")
        assert blocks[notify + 1] == "trigger.BLOCK_BRANCH_ALWAYS, block(0)"
    assert blocks[12] == "trigger.BLOCK_MEASURE_DIGITIZE, oe_m5_buffer, 1"
    assert (
        "trigger.LIMIT_OUTSIDE, lower_cutoff_v, upper_cutoff_v, block(24), block(12)" in blocks[13]
    )
    assert blocks[17] == "trigger.BLOCK_WAIT, trigger.EVENT_TIMER2, trigger.CLEAR_NEVER"
    assert blocks[22] == "trigger.BLOCK_WAIT, trigger.EVENT_TIMER3, trigger.CLEAR_NEVER"
    assert blocks[15] == "trigger.BLOCK_WAIT, trigger.EVENT_TIMER4, trigger.CLEAR_NEVER"
    assert "trigger.timer[4].delaylist = deadlines" in source
    assert "local deadlines = {sample_period_s / 2}" in source
    assert "deadlines[index] = sample_period_s" in source
    assert "trigger.timer[4].count = measurement_count" in source
    assert "trigger.timer[3].count = measurement_count" in source


def test_tsp_abort_recovery_and_initialization_disable_all_added_timers() -> None:
    source = timed_packaged_runtime().source[len(packaged_runtime().source) :]
    for timer in (2, 3, 4):
        assert f"trigger.timer[{timer}].enable = trigger.OFF" in source
    for name in ("initialize", "abort", "force_safe", "recover"):
        body = source.split(f"function oe_m4_{name}()", 1)[1].split("\nend", 1)[0]
        assert "oe_timed_disable_timers()" in body
    recovery = source.split("function oe_m4_recover()", 1)[1]
    assert recovery.index("oe_timed_base_recover()") < recovery.index("oe_timed_disable_timers()")
    assert "oe_m5_buffer.clear()" not in source
    refresh = source.split("function oe_m4_refresh_state()", 1)[1].split(
        "function oe_timed_terminal_cause()", 1
    )[0]
    assert "oe_timed_disable_timers()" not in refresh


def test_new_two_volt_profile_programs_and_verifies_higher_resistor_signal() -> None:
    original = config()
    higher = replace(
        original,
        safety=replace(original.safety, voltage_min_v=-1, voltage_max_v=1),
    )
    value = plan(
        current_a=0.005,
        source_range_a=0.01,
        voltage_limit_v=1,
        measurement_profile=K2460MeasurementProfile.NPLC_1_2V,
    )
    value.validate_config(higher)
    transport = FakeTransport(
        TIMED_RUNTIME_BUILD,
        "false",
        status(),
        "0\t0\t0\t16",
        "4\t1\t2\t0\t0\t0\t0",
        "1\t0.1\t10\t-1\t1\t0.05",
        "0.005\t0.01\t1",
        "|".join(f"{i}) NOP" for i in range(1, 30)),
    )
    result = TimedHoldRuntimeController(transport, higher).prepare_timed_hold(value)
    assert result.state == RuntimeState.PREPARED
    assert transport.commands[2].endswith(",1,1,1,4)")
    transferred = transport.commands[2].split("(", 1)[1].rstrip(")").split(",")
    assert tuple(float(field) for field in transferred[:3]) == (0.005, 0.01, 1)
    source = timed_packaged_runtime().source[len(packaged_runtime().source) :]
    assert "setup_voltage_limit = math.min(voltage_limit_v, 0.2)" in source
    assert "setup_profile = 1" in source
    assert "smu.source.vlimit.level = voltage_limit_v" in source
    assert "smu.measure.range = 2" in source
    assert "oe_m5_measurement_profile_code = 4" in source
    setup = source.split("function oe_timed_prepare_current_hold(", 1)[1]
    assert setup.index("smu.measure.range = 0.2") < setup.index(
        "oe_m5_prepare_profiled_current_hold_acquisition("
    )
    assert setup.index("smu.measure.range = 2\n") < setup.index(
        "smu.source.vlimit.level = voltage_limit_v"
    )
    assert setup.index("smu.source.output ~= smu.OFF") < setup.index("smu.measure.range = 0.2")


@pytest.mark.parametrize("readback", ["0.005\t0.01\t0.2", "0.001\t0.01\t0.2", "nan"])
def test_source_level_range_and_compliance_readback_must_match(readback: str) -> None:
    replies = prepare_replies()[:-2] + (readback,)
    with pytest.raises(TransportProtocolError, match="source"):
        TimedHoldRuntimeController(FakeTransport(*replies), config()).prepare_timed_hold(plan())


def test_two_volt_profile_rejects_compliance_below_its_fixed_range_minimum():
    with pytest.raises(ValidationError, match="measurement range"):
        plan(voltage_limit_v=0.2, measurement_profile=K2460MeasurementProfile.NPLC_1_2V)


@pytest.mark.parametrize("prior", [15, 16])
def test_known_candidate_replacement_refuses_retained_data_before_deletion(prior):
    stopped = status("idle", model="trigger.STATE_BUILDING")
    transport = FakeTransport(
        stopped,
        stopped,
        "0",
        "oe-k2460-m4-hold-v1",
        f"m4-finite-current-hold-v{prior}",
        "false",
        "false",
        "1\t1\t1\t16",
    )
    with pytest.raises(IncompatibleInstrumentError, match="retained"):
        getattr(
            TimedHoldRuntimeController(transport, config()), f"replace_known_v{prior}_and_install"
        )()
    assert transport.loaded is None
    assert not any(
        command.startswith(("script.delete", "buffer.delete")) for command in transport.commands
    )


@pytest.mark.parametrize("prior", [15, 16])
def test_known_empty_candidate_replacement_checks_exact_identity_and_owned_globals(prior):
    stopped = status("idle", model="trigger.STATE_BUILDING")
    transport = FakeTransport(
        stopped,
        stopped,
        "0",
        "oe-k2460-m4-hold-v1",
        f"m4-finite-current-hold-v{prior}",
        "false",
        "false",
        "0\t0\t0\t16",
        "true",
        "true",
        "0",
        "0",
        "true",
    )
    result = getattr(
        TimedHoldRuntimeController(transport, config()), f"replace_known_v{prior}_and_install"
    )()
    assert transport.loaded == (result.source, result.abi, result.sha256)
    old_script = {15: "oe_m4_d8d3bcdb948dde40be53f71d", 16: "oe_m4_74194bbe335d7e3950f25143"}[prior]
    assert f'script.delete("{old_script}")' in transport.commands
    assert "oe_timed_prepare_current_hold = nil" in transport.commands
    assert "oe_timed_terminal_cause = nil" in transport.commands
    assert not any("defbuffer" in command for command in transport.commands)


@pytest.mark.parametrize(
    "graph", ["EMPTY", "1) NOP|3) NOP", "|".join(f"{i}) NOP" for i in range(1, 40))]
)
def test_prepare_rejects_missing_or_wrong_mode_firmware_graph_before_arm(graph):
    transport = FakeTransport(*prepare_replies()[:-1], graph)
    with pytest.raises(TransportProtocolError, match="graph"):
        TimedHoldRuntimeController(transport, config()).prepare_timed_hold(plan())
    assert "oe_m4_arm()" not in transport.commands


def test_prepare_accepts_actual_padded_firmware_graph_with_latency_note():
    graph = (
        (Path(__file__).parents[1] / "fixtures" / "k2460-v17-immediate-block-list.txt")
        .read_text()
        .strip()
    )
    transport = FakeTransport(*prepare_replies()[:-1], graph)
    result = TimedHoldRuntimeController(transport, config()).prepare_timed_hold(plan())
    assert result.state == RuntimeState.PREPARED and not result.output_enabled
    assert "oe_m4_arm()" not in transport.commands
