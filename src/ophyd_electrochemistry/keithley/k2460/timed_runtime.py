"""Uncommissioned, packaged candidate for resistor-only timed current holds.

The new model programs a duration and sample cadence independently. Its local
half-slot checks are candidate deadline logic, not established physical timing
or protection latency. No hardware acceptance gate is promoted by this module.
"""

import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal
from importlib.resources import files

from ...acquisition import StartMode
from ...exceptions import IncompatibleInstrumentError, TransportProtocolError, ValidationError
from ...validation import number
from .config import Keithley2460Config
from .measurement_profiles import K2460MeasurementProfile, resolve_measurement_profile
from .runtime import (
    _RUNTIME_GLOBALS_V14,
    CurrentHoldAcquisitionProof,
    M4RuntimeController,
    RuntimeArtifact,
    RuntimeState,
    RuntimeStatus,
    _tsp_number,
    _tsp_start_edge,
    packaged_runtime,
)

TIMED_RUNTIME_BUILD = "m4-finite-current-hold-v17"
TIMED_RUNTIME_RESOURCE = "tsp/timed_hold.tsp"
TIMED_SOURCE_RANGES_A = (1e-6, 1e-5, 1e-4, 1e-3, 1e-2)
_STOPPED_MODEL_STATES = {
    "trigger.STATE_IDLE",
    "trigger.STATE_BUILDING",
    "trigger.STATE_EMPTY",
    "trigger.STATE_ABORTED",
    "trigger.STATE_FAILED",
}
_TIMED_GLOBALS = (
    "oe_timed_enabled",
    "oe_timed_cause",
    "oe_timed_count",
    "oe_timed_lower_cutoff",
    "oe_timed_upper_cutoff",
    "oe_timed_prepare_current_hold",
    "oe_timed_plan_status",
    "oe_timed_terminal_cause",
)
_TERMINAL_CAUSES = frozenset(
    (
        "not_started",
        "prepared",
        "running",
        "normal_completion",
        "start_timeout",
        "operator_abort",
        "voltage_cutoff",
        "measurement_deadline",
        "cadence_overrun",
        "runtime_error",
    )
)


@dataclass(frozen=True, kw_only=True)
class TimedHoldPlan:
    """Finite nominal schedule for a guarded resistor commissioning acquisition.

    Measurements are requested at nominal offsets ``0, P, ..., duration-P``.
    Duration must be an integer multiple of P. The half-slot budget is a model
    deadline to test, and does not assert a measured maximum measurement time.
    """

    current_a: float
    source_range_a: float
    voltage_limit_v: float
    duration_s: float
    sample_period_s: float
    lower_cutoff_v: float | None = None
    upper_cutoff_v: float | None = None
    start_mode: StartMode = StartMode.IMMEDIATE
    measurement_profile: K2460MeasurementProfile = K2460MeasurementProfile.NPLC_1

    def __post_init__(self) -> None:
        for name in (
            "current_a",
            "source_range_a",
            "voltage_limit_v",
            "duration_s",
            "sample_period_s",
        ):
            object.__setattr__(self, name, number(getattr(self, name), name))
        for name in ("lower_cutoff_v", "upper_cutoff_v"):
            if getattr(self, name) is not None:
                object.__setattr__(self, name, number(getattr(self, name), name))
        if self.source_range_a not in TIMED_SOURCE_RANGES_A:
            raise ValidationError("Timed hold source range must be an allowlisted fixed range")
        if abs(self.current_a) > self.source_range_a:
            raise ValidationError("Timed hold current exceeds its source range")
        settings = resolve_measurement_profile(self.measurement_profile)
        # Rev. C 14-194; the 2 V profile's lower compliance is 0.212 V.
        if (
            not 0.106 * settings.voltage_range_v
            <= self.voltage_limit_v
            <= min(2.0, settings.voltage_range_v)
        ):
            raise ValidationError("Timed hold voltage limit exceeds its fixed measurement range")
        if not 0.1 <= self.sample_period_s <= self.duration_s <= 60:
            raise ValidationError("Timed hold requires 0.1 <= sample period <= duration <= 60 s")
        quotient = Decimal(str(self.duration_s)) / Decimal(str(self.sample_period_s))
        if quotient != quotient.to_integral_value():
            raise ValidationError(
                "Timed hold duration must be an integer multiple of sample period"
            )
        if self.measurement_count > 600:
            raise ValidationError("Timed hold measurement count must not exceed 600")
        if not isinstance(self.start_mode, StartMode):
            raise ValidationError("start_mode must be a StartMode")
        resolve_measurement_profile(self.measurement_profile)
        if (
            self.lower_cutoff_v is not None
            and self.upper_cutoff_v is not None
            and self.lower_cutoff_v >= self.upper_cutoff_v
        ):
            raise ValidationError("Timed hold lower cutoff must be below upper cutoff")

    @property
    def measurement_count(self) -> int:
        return int(Decimal(str(self.duration_s)) / Decimal(str(self.sample_period_s)))

    @property
    def measurement_budget_s(self) -> float:
        return self.sample_period_s / 2

    def effective_voltage_cutoffs(self, config: Keithley2460Config) -> tuple[float, float]:
        safety = config.safety
        lower = max(
            -self.voltage_limit_v,
            safety.voltage_min_v,
            safety.voltage_min_v if self.lower_cutoff_v is None else self.lower_cutoff_v,
        )
        upper = min(
            self.voltage_limit_v,
            safety.voltage_max_v,
            safety.voltage_max_v if self.upper_cutoff_v is None else self.upper_cutoff_v,
        )
        if lower >= upper:
            raise ValidationError("Timed hold cutoff intersection is empty")
        return lower, upper

    def validate_config(self, config: Keithley2460Config) -> None:
        """Check all host-side bounds without accessing a transport."""
        if not isinstance(config, Keithley2460Config):
            raise ValidationError("Timed hold requires Keithley2460Config")
        safety = config.safety
        if not safety.voltage_min_v < 0 < safety.voltage_max_v:
            raise ValidationError(
                "Timed resistor hold requires a voltage safety envelope spanning zero"
            )
        if self.voltage_limit_v > min(-safety.voltage_min_v, safety.voltage_max_v):
            raise ValidationError(
                "Timed hold compliance exceeds a directional voltage safety bound"
            )
        current_limit = (
            safety.charge_current_max_a if self.current_a >= 0 else safety.discharge_current_max_a
        )
        if abs(self.current_a) > current_limit:
            raise ValidationError("Timed hold current exceeds configured directional safety limit")
        if abs(self.current_a) * self.voltage_limit_v > safety.power_abs_max_w:
            raise ValidationError("Timed hold worst-case power exceeds configured safety limit")
        lower, upper = self.effective_voltage_cutoffs(config)
        measurement_range = resolve_measurement_profile(self.measurement_profile).voltage_range_v
        if lower < -measurement_range or upper > measurement_range:
            raise ValidationError("Timed hold cutoffs exceed the fixed measurement range")
        if max(16, self.measurement_count) > config.buffer.capacity_ceiling_records:
            raise ValidationError("Timed hold measurement count exceeds configured capacity")
        if config.io.external_abort_enabled:
            raise ValidationError("External ABORT remains disabled for the timed candidate")
        if safety.source_off_mode.lower() != "normal":
            raise ValidationError("Timed candidate supports only NORMAL output-off mode")
        if not 0.001 <= config.timing.external_start_timeout_s <= 60:
            raise ValidationError("Timed hold START timeout must be in 0.001..60 s")
        if not 0.001 <= config.timing.poll_period_s <= 0.1:
            raise ValidationError("Timed hold event poll must be in 0.001..0.1 s")


def timed_packaged_runtime() -> RuntimeArtifact:
    """Return the exact v14 wire artifact plus its reviewed candidate extension."""
    base = packaged_runtime()
    readable = files(__package__).joinpath(TIMED_RUNTIME_RESOURCE).read_text(encoding="ascii")
    if not readable.endswith("\n") or "\r" in readable:
        raise RuntimeError("Packaged timed extension must use LF framing and end with LF")
    extension = "\n".join(line for line in readable.splitlines() if line) + "\n"
    source = base.source + extension
    if f'oe_m4_runtime_build = "{TIMED_RUNTIME_BUILD}"' not in extension:
        raise RuntimeError("Packaged timed extension build does not match its host adapter")
    digest = hashlib.sha256(source.encode("ascii")).hexdigest()
    return RuntimeArtifact(
        abi=base.abi,
        build=TIMED_RUNTIME_BUILD,
        source=source,
        sha256=digest,
        script_name=f"oe_m4_{digest[:24]}",
    )


class TimedHoldRuntimeController(M4RuntimeController):
    """Candidate timed model; installation is always an explicit separate action."""

    def install(self) -> RuntimeArtifact:
        if self.transport.command_language != "TSP":
            raise IncompatibleInstrumentError("Timed runtime installation requires TSP mode")
        artifact = timed_packaged_runtime()
        absent = self.transport.query("print(oe_m4_runtime_abi == nil and oe_m4_initialize == nil)")
        if absent not in ("true", "false"):
            raise TransportProtocolError("Cannot determine whether runtime globals exist")
        if absent == "false":
            if self.transport.query("print(oe_m4_runtime_build)") != TIMED_RUNTIME_BUILD:
                raise IncompatibleInstrumentError(
                    "Existing runtime requires explicit guarded replacement "
                    "before timed installation"
                )
            status = self.status()
            if (
                status.state != RuntimeState.IDLE
                or status.output_enabled
                or status.model_state not in _STOPPED_MODEL_STATES
                or self._query_source_level() != 0
            ):
                raise IncompatibleInstrumentError(
                    "Timed runtime reinstall requires stopped IDLE/OFF/0 A"
                )
            if self.buffer_info().record_count:
                raise IncompatibleInstrumentError(
                    "Timed runtime reinstall must preserve retained records"
                )
        self.transport.load_runtime(artifact.source, abi=artifact.abi, sha256=artifact.sha256)
        return artifact

    def replace_known_v14_and_install(self) -> RuntimeArtifact:
        """Explicit replacement of only the exact empty, stopped v14 artifact."""
        status = self.status()
        if status.model_state not in _STOPPED_MODEL_STATES:
            raise IncompatibleInstrumentError(
                "Runtime replacement requires a stopped trigger model"
            )
        base = packaged_runtime()
        return self._replace_known_runtime_and_install(
            {base.build: (base.script_name, _RUNTIME_GLOBALS_V14, True)}
        )

    def replace_known_v15_and_install(self) -> RuntimeArtifact:
        """Replace only the exact first v15 candidate after explicit disposition."""
        if self.status().model_state not in _STOPPED_MODEL_STATES:
            raise IncompatibleInstrumentError(
                "Runtime replacement requires a stopped trigger model"
            )
        return self._replace_known_runtime_and_install(
            {
                "m4-finite-current-hold-v15": (
                    "oe_m4_d8d3bcdb948dde40be53f71d",
                    _RUNTIME_GLOBALS_V14 + _TIMED_GLOBALS,
                    True,
                )
            }
        )

    def replace_known_v16_and_install(self) -> RuntimeArtifact:
        """Replace only the exact empty, stopped v16 commissioning candidate."""
        if self.status().model_state not in _STOPPED_MODEL_STATES:
            raise IncompatibleInstrumentError(
                "Runtime replacement requires a stopped trigger model"
            )
        return self._replace_known_runtime_and_install(
            {
                "m4-finite-current-hold-v16": (
                    "oe_m4_74194bbe335d7e3950f25143",
                    _RUNTIME_GLOBALS_V14 + _TIMED_GLOBALS,
                    True,
                )
            }
        )

    def prepare_timed_hold(self, plan: TimedHoldPlan) -> RuntimeStatus:
        if not isinstance(plan, TimedHoldPlan):
            raise ValidationError("plan must be TimedHoldPlan")
        plan.validate_config(self.config)
        self._validate_runtime_config()
        if self.transport.query("print(oe_m4_runtime_build)") != TIMED_RUNTIME_BUILD:
            raise IncompatibleInstrumentError("Timed hold requires the exact packaged timed build")
        artifact = timed_packaged_runtime()
        if self.transport.query(f"print({artifact.script_name} == nil)") != "false":
            raise IncompatibleInstrumentError("Exact digest-named timed runtime script is absent")
        io = self.config.io
        lower, upper = plan.effective_voltage_cutoffs(self.config)
        profile = resolve_measurement_profile(plan.measurement_profile)
        values = (
            _tsp_number(plan.current_a),
            _tsp_number(plan.source_range_a),
            _tsp_number(plan.voltage_limit_v),
            _tsp_number(plan.duration_s),
            _tsp_number(plan.sample_period_s),
            str(plan.measurement_count),
            _tsp_number(lower),
            _tsp_number(upper),
            "1" if plan.start_mode == StartMode.EXTERNAL_TRIGGER else "0",
            _tsp_number(self.config.timing.external_start_timeout_s),
            _tsp_number(self.config.timing.poll_period_s),
            "1" if self.config.source_terminal == "front" else "0",
            "1" if self.config.sense == "remote" else "0",
            str(io.ready),
            str(io.busy),
            str(io.start),
            str(io.ready_asserted_level),
            str(io.busy_asserted_level),
            _tsp_start_edge(io.start_edge),
            str(profile.instrument_code),
        )
        self.transport.write("oe_timed_prepare_current_hold(" + ",".join(values) + ")")
        status = self.status()
        if status.state != RuntimeState.PREPARED or status.output_enabled:
            raise TransportProtocolError("Timed hold did not reach output-OFF PREPARED")
        info = self.buffer_info()
        if info.record_count or info.capacity_records < plan.measurement_count:
            raise TransportProtocolError("Timed hold buffer is not empty or sufficiently large")
        self._confirm_measurement_profile(plan.measurement_profile)
        fields = self.transport.query("print(oe_timed_plan_status())").split("\t")
        try:
            actual = tuple(number(float(value), "timed plan readback") for value in fields)
        except (ValueError, ValidationError) as exc:
            raise TransportProtocolError("Timed hold plan readback is invalid") from exc
        expected = (
            plan.duration_s,
            plan.sample_period_s,
            float(plan.measurement_count),
            lower,
            upper,
            plan.measurement_budget_s,
        )
        if actual != expected:
            raise TransportProtocolError("Timed hold plan readback does not match the request")
        source_fields = self.transport.query(
            "print(smu.source.level, smu.source.range, smu.source.vlimit.level)"
        ).split("\t")
        try:
            source_actual = tuple(
                number(float(value), "timed source readback") for value in source_fields
            )
        except (ValueError, ValidationError) as exc:
            raise TransportProtocolError("Timed hold source readback is invalid") from exc
        if source_actual != (plan.current_a, plan.source_range_a, plan.voltage_limit_v):
            raise TransportProtocolError("Timed hold source settings do not match the request")
        # Rev. C 8-47/48: every numbered block must exist. Inspect the actual
        # firmware graph before arm, with embedded newlines escaped for framing.
        graph = self.transport.query(
            'print((string.gsub(trigger.model.getblocklist(), "\\n", "|")))'
        )
        labels = tuple(int(match) for match in re.findall(r"(?:^|[|])\s*([0-9]+)\)", graph))
        count = 39 if plan.start_mode == StartMode.EXTERNAL_TRIGGER else 29
        if labels != tuple(range(1, count + 1)):
            raise TransportProtocolError("Timed trigger graph has missing or unexpected blocks")
        return status

    def arm(self) -> RuntimeStatus:
        self.transport.write("oe_m4_arm()")
        status = self.status()
        if status.state not in (
            RuntimeState.WAITING_START,
            RuntimeState.RUNNING,
            RuntimeState.COMPLETE,
            RuntimeState.START_TIMEOUT,
            RuntimeState.ERROR,
        ):
            raise TransportProtocolError(f"Timed runtime entered unexpected state {status.state}")
        return status

    def terminal_cause(self) -> str:
        cause = self.transport.query("print(oe_timed_terminal_cause())")
        if cause not in _TERMINAL_CAUSES:
            raise TransportProtocolError("Timed runtime returned an unknown terminal cause")
        return cause

    def prepare_current_hold_acquisition(
        self, acquisition: CurrentHoldAcquisitionProof
    ) -> RuntimeStatus:
        raise ValidationError(
            "Timed candidate requires prepare_timed_hold with an explicit schedule"
        )
