"""M1 pure planning compiler: bounded IR, not executable TSP/TriggerFlow."""

import math
from bisect import bisect_right
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from itertools import accumulate

from ...acquisition import AcquisitionRequest, StartMode
from ...exceptions import UnsupportedCapabilityError, ValidationError
from ...protocols import (
    CurrentPulseSequence,
    CyclicVoltammetry,
    GalvanostaticHold,
    PotentiostaticHold,
)
from ...serialization import canonical_json, canonical_request_json, sha256_json
from ...validation import integer, number
from ...waveforms import (
    PRBS_TAPS,
    ArbitraryWaveform,
    PRBSWaveform,
    SourceFunction,
    multisine_levels,
    prbs_bits,
)
from .capabilities import Keithley2460Capabilities, SourceCapabilities
from .config import Keithley2460Config

IR_SCHEMA = "ophyd-electrochemistry/finite-ir-v1"
COMPILER_VERSION = "m1-compiler-v1"


def _decimal(value: float) -> Decimal:
    return Decimal(str(value))


@dataclass(frozen=True, kw_only=True)
class SourceStep:
    level: float
    dwell_ticks: int
    cycle_index: int = 0
    segment_index: int = 0


@dataclass(frozen=True, kw_only=True)
class SourceSchedule:
    steps: tuple[SourceStep, ...]
    repeats: int
    boundaries_ticks: tuple[int, ...]

    @property
    def period_ticks(self) -> int:
        return self.boundaries_ticks[-1]

    @property
    def duration_ticks(self) -> int:
        return self.period_ticks * self.repeats

    @property
    def logical_points(self) -> int:
        return len(self.steps) * self.repeats

    def point_at_tick(self, tick: int) -> tuple[int, int, int]:
        integer(tick, "tick", minimum=0)
        if tick >= self.duration_ticks:
            raise ValidationError("Tick lies outside the finite source schedule")
        repeat, local = divmod(tick, self.period_ticks)
        point = bisect_right(self.boundaries_ticks, local) - 1
        return repeat * len(self.steps) + point, repeat, point


@dataclass(frozen=True, kw_only=True)
class MeasurementSchedule:
    first_tick: int
    period_ticks: int
    aperture_ticks: int
    overhead_ticks: int
    record_count: int


@dataclass(frozen=True, kw_only=True)
class SampleMapping:
    sample_index: int
    aperture_start_tick: int
    aperture_end_tick: int
    first_logical_point: int
    last_logical_point: int
    repeat_index: int
    point_index: int
    wholly_settled_in_one_dwell: bool


@dataclass(frozen=True, kw_only=True)
class ResourceBudget:
    unique_points: int
    logical_points: int
    measurement_records: int
    source_records: int
    buffer_records: int
    trigger_block_estimate: int
    upload_bytes: int


@dataclass(frozen=True, kw_only=True)
class CompiledProgram:
    canonical_request_json: str
    request_sha256: str
    canonical_program_json: str
    program_sha256: str
    waveform_sha256: str
    generator_provenance_json: str
    source_function: SourceFunction
    source: SourceSchedule
    measurement: MeasurementSchedule
    timer_resolution_s: float
    settling_ticks: int
    requested_duration_s: float
    achieved_duration_s: float
    achieved_source_period_s: float | None
    achieved_sample_period_s: float
    achieved_scan_rates_v_per_s: tuple[float, ...]
    achieved_tone_frequencies_hz: tuple[float, ...]
    mean_level: float
    ac_rms_level: float
    ac_crest_factor: float
    commanded_charge_c: float | None
    effective_voltage_cutoffs_v: tuple[float, float] | None
    budget: ResourceBudget
    capability_profile_id: str
    capability_evidence_kind: str
    ir_schema: str = IR_SCHEMA
    hardware_ready: bool = False

    def sample_mapping(self, sample_index: int) -> SampleMapping:
        integer(sample_index, "sample_index", minimum=0)
        if sample_index >= self.measurement.record_count:
            raise ValidationError("Sample index is outside the finite schedule")
        start = self.measurement.first_tick + sample_index * self.measurement.period_ticks
        end = start + self.measurement.aperture_ticks
        first, repeat, point = self.source.point_at_tick(start)
        last, _, _ = self.source.point_at_tick(end - 1)
        point_start = repeat * self.source.period_ticks + self.source.boundaries_ticks[point]
        return SampleMapping(
            sample_index=sample_index,
            aperture_start_tick=start,
            aperture_end_tick=end,
            first_logical_point=first,
            last_logical_point=last,
            repeat_index=repeat,
            point_index=point,
            wholly_settled_in_one_dwell=first == last
            and start - point_start >= self.settling_ticks,
        )


class Keithley2460Compiler:
    """Require explicit planning capabilities; never connect, arm or approve hardware."""

    def __init__(self, capabilities: Keithley2460Capabilities):
        if not isinstance(capabilities, Keithley2460Capabilities):
            raise ValidationError("Explicit Keithley2460Capabilities are required")
        self.capabilities = capabilities

    def _ticks(self, value: float, tolerance: float, label: str) -> int:
        resolution, requested = _decimal(self.capabilities.timer_resolution_s), _decimal(value)
        ticks = int((requested / resolution).to_integral_value(rounding=ROUND_HALF_UP))
        if ticks <= 0 or abs(ticks * resolution - requested) > _decimal(tolerance):
            raise ValidationError(f"{label} cannot meet its timing tolerance")
        return ticks

    def _profile_ticks(self, value: float, label: str) -> int:
        return 0 if value == 0 else self._ticks(value, 0.0, label)

    def _seconds(self, ticks: int) -> float:
        return number(
            float(ticks * _decimal(self.capabilities.timer_resolution_s)), "tick duration"
        )

    def compile(self, request: AcquisitionRequest, config: Keithley2460Config) -> CompiledProgram:
        if not isinstance(request, AcquisitionRequest) or not isinstance(
            config, Keithley2460Config
        ):
            raise ValidationError("Compile requires AcquisitionRequest and Keithley2460Config")
        caps, p = self.capabilities, request.program
        self._validate_config(request, config)
        function: SourceFunction
        if isinstance(p, (PotentiostaticHold, CyclicVoltammetry)):
            function, compliance = "voltage", p.current_limit_a
        elif isinstance(p, (GalvanostaticHold, CurrentPulseSequence)):
            function, compliance = "current", p.voltage_limit_v
        else:
            function = p.source_function
            value = p.voltage_limit_v if function == "current" else p.current_limit_a
            assert value is not None
            compliance = value
        profile = next((s for s in caps.sources if s.source_function == function), None)
        if profile is None:
            raise UnsupportedCapabilityError(f"{function} sourcing is not declared")
        if not profile.compliance_min <= compliance <= profile.compliance_max:
            raise ValidationError("Complementary compliance is outside capability limits")
        cutoffs = self._effective_cutoffs(request, config, function, compliance)
        source, requested_duration, rates, tones, source_period = self._source(request, config)
        tick_s = caps.timer_resolution_s
        duration = self._seconds(source.duration_ticks)
        if duration > caps.max_duration_s:
            raise ValidationError("Maximum finite duration exceeded")
        if abs(_decimal(duration) - _decimal(requested_duration)) > _decimal(
            config.timing.duration_tolerance_s
        ):
            raise ValidationError("Accumulated quantization exceeds duration_tolerance_s")
        self._validate_levels(source, profile, config, function, compliance)
        if isinstance(p, ArbitraryWaveform) and p.multisine_spec is not None:
            self._validate_multisine(p, profile, config, function, compliance)
        if source.logical_points > 1 and not caps.supports_independent_measurement:
            raise UnsupportedCapabilityError(
                "Independent source/measurement scheduling is unsupported"
            )
        settling = self._profile_ticks(
            caps.source_action_time_s, "source action"
        ) + self._profile_ticks(caps.source_settling_time_s, "settling time")
        aperture = self._profile_ticks(caps.measurement_aperture_s, "measurement aperture")
        overhead = self._profile_ticks(caps.measurement_overhead_s, "measurement overhead")
        sample_ticks = self._ticks(
            p.sample_period_s, config.timing.measurement_period_tolerance_s, "measurement period"
        )
        if (
            self._seconds(sample_ticks) < caps.min_measurement_period_s
            or sample_ticks < aperture + overhead
        ):
            raise ValidationError("Measurement period cannot fit aperture/overhead/profile minimum")
        if isinstance(p, CyclicVoltammetry) and sample_ticks < settling + aperture + overhead:
            raise ValidationError("CV dwell cannot fit a complete settled endpoint measurement")
        if function == "current" and caps.local_cutoffs_use_measurements:
            cutoff_bound = (
                self._seconds(max(sample_ticks, settling) + aperture + overhead)
                + caps.cutoff_latency_s
            )
            if cutoff_bound > config.timing.required_cutoff_latency_s:
                raise UnsupportedCapabilityError("Measurement cadence cannot meet cutoff latency")
        available = source.duration_ticks - settling - aperture - overhead
        if available < 0:
            raise ValidationError("Program cannot fit one complete settled measurement")
        count = available // sample_ticks + 1
        source_records = source.logical_points * caps.source_records_per_point
        blocks = caps.fixed_trigger_blocks + len(source.steps) * caps.trigger_blocks_per_point
        if count > caps.max_measurement_records or count + source_records > caps.max_buffer_records:
            raise ValidationError("Measurement/source buffer budget exceeded")
        if blocks > caps.max_trigger_blocks:
            raise ValidationError("Declared trigger-block layout budget exceeded")
        measurement = MeasurementSchedule(
            first_tick=settling,
            period_ticks=sample_ticks,
            aperture_ticks=aperture,
            overhead_ticks=overhead,
            record_count=count,
        )
        weights = tuple(s.dwell_ticks / source.period_ticks for s in source.steps)
        mean = number(
            math.fsum(s.level * w for s, w in zip(source.steps, weights, strict=True)), "mean"
        )
        ac = tuple(s.level - mean for s in source.steps)
        scale = max(abs(v) for v in ac)
        rms = (
            0.0
            if scale == 0
            else number(
                scale
                * math.sqrt(
                    math.fsum((v / scale) ** 2 * w for v, w in zip(ac, weights, strict=True))
                ),
                "AC RMS",
            )
        )
        crest = 0.0 if rms == 0 else number(scale / rms, "AC crest factor")
        charge = number(mean * duration, "commanded charge") if function == "current" else None
        waveform_json = canonical_json({"source": source, "timer_resolution_s": tick_s})
        request_json = canonical_request_json(request)
        provenance = {}
        if isinstance(p, PRBSWaveform):
            provenance = {
                "kind": "prbs",
                "generator_id": p.generator_id,
                "order": p.order,
                "seed": p.seed,
                "xor_taps_zero_based": PRBS_TAPS[p.order],
                "polynomial_exponents": (p.order, *reversed(PRBS_TAPS[p.order])),
                "convention": "output-lsb-before-right-shift-xor-to-msb",
            }
        elif isinstance(p, ArbitraryWaveform) and p.multisine_spec is not None:
            spec = p.multisine_spec
            provenance = {
                "kind": "multisine",
                "spec": spec,
                "tone_bins": spec.tone_bins,
                "achieved_frequencies_hz": tones,
                "analytical_envelope": (
                    spec.bias - math.fsum(spec.amplitudes),
                    spec.bias + math.fsum(spec.amplitudes),
                ),
            }
        encoded = canonical_json(
            {
                "schema": IR_SCHEMA,
                "compiler": COMPILER_VERSION,
                "hardware_ready": False,
                "request": request,
                "config": config,
                "capabilities": caps,
                "source_function": function,
                "source": source,
                "measurement": measurement,
                "voltage_cutoffs": cutoffs,
                "cutoff_policy": "fault",
                "terminal_action": "output_off",
                "requested_duration_s": requested_duration,
                "achieved_scan_rates": rates,
                "achieved_tone_frequencies": tones,
                "mean": mean,
                "ac_rms": rms,
                "crest_factor": crest,
                "commanded_charge_c": charge,
                "trigger_block_estimate": blocks,
                "source_records": source_records,
                "buffer_records": count + source_records,
                "generator_provenance": provenance,
            }
        )
        size = len(encoded.encode("utf-8"))
        if size > caps.max_upload_bytes:
            raise ValidationError("Serialized planning payload exceeds upload budget")
        budget = ResourceBudget(
            unique_points=len(source.steps),
            logical_points=source.logical_points,
            measurement_records=count,
            source_records=source_records,
            buffer_records=count + source_records,
            trigger_block_estimate=blocks,
            upload_bytes=size,
        )
        result = CompiledProgram(
            canonical_request_json=request_json,
            request_sha256=sha256_json(request_json),
            canonical_program_json=encoded,
            program_sha256=sha256_json(encoded),
            waveform_sha256=sha256_json(waveform_json),
            generator_provenance_json=canonical_json(provenance),
            source_function=function,
            source=source,
            measurement=measurement,
            timer_resolution_s=tick_s,
            settling_ticks=settling,
            requested_duration_s=requested_duration,
            achieved_duration_s=duration,
            achieved_source_period_s=source_period,
            achieved_sample_period_s=self._seconds(sample_ticks),
            achieved_scan_rates_v_per_s=rates,
            achieved_tone_frequencies_hz=tones,
            mean_level=mean,
            ac_rms_level=rms,
            ac_crest_factor=crest,
            commanded_charge_c=charge,
            effective_voltage_cutoffs_v=cutoffs,
            budget=budget,
            capability_profile_id=caps.profile_id,
            capability_evidence_kind=caps.evidence_kind,
        )
        if not caps.aperture_may_cross_updates:
            for index in range(count):
                if not result.sample_mapping(index).wholly_settled_in_one_dwell:
                    raise UnsupportedCapabilityError(
                        "Measurement overlaps an update/settling window"
                    )
        return result

    def _validate_config(self, request: AcquisitionRequest, config: Keithley2460Config) -> None:
        caps = self.capabilities
        if config.safety.source_off_mode not in caps.supported_off_modes:
            raise UnsupportedCapabilityError("OFF mode is not supported by the profile")
        if (
            config.source_terminal not in caps.supported_terminals
            or config.sense not in caps.supported_sense_modes
        ):
            raise UnsupportedCapabilityError("Terminals/sense mode are unsupported")
        if request.start_mode == StartMode.EXTERNAL_TRIGGER and not caps.supports_external_start:
            raise UnsupportedCapabilityError("External START is unsupported")
        if config.io.external_abort_enabled and not caps.supports_external_abort:
            raise UnsupportedCapabilityError("External ABORT remains unsupported")
        if caps.abort_latency_s > config.timing.required_abort_latency_s:
            raise UnsupportedCapabilityError("Declared abort latency exceeds requirement")
        if config.timing.shutdown_timeout_s < caps.abort_latency_s:
            raise ValidationError("Shutdown timeout is below declared abort latency")

    def _effective_cutoffs(
        self,
        request: AcquisitionRequest,
        config: Keithley2460Config,
        function: SourceFunction,
        compliance: float,
    ) -> tuple[float, float] | None:
        safety, caps = config.safety, self.capabilities
        if function == "voltage":
            if compliance > min(safety.charge_current_max_a, safety.discharge_current_max_a):
                raise ValidationError("Current compliance exceeds bidirectional cell limits")
            return None
        if compliance > max(abs(safety.voltage_min_v), abs(safety.voltage_max_v)):
            raise ValidationError("Voltage compliance exceeds cell envelope")
        if (
            not caps.supports_local_cutoffs
            or caps.cutoff_latency_s > config.timing.required_cutoff_latency_s
        ):
            raise UnsupportedCapabilityError("Local cutoff policy/latency is unsupported")
        lower = getattr(request.program, "lower_cutoff_v", None)
        upper = getattr(request.program, "upper_cutoff_v", None)
        lower = safety.voltage_min_v if lower is None else lower
        upper = safety.voltage_max_v if upper is None else upper
        if not safety.voltage_min_v <= lower < upper <= safety.voltage_max_v:
            raise ValidationError("Cutoffs must narrow the safety voltage envelope")
        return lower, upper

    def _point_budget(self, unique: int, repeats: int) -> None:
        caps = self.capabilities
        if unique > caps.max_unique_points or unique * repeats > caps.max_logical_points:
            raise ValidationError("Source point/list/repeat budget exceeded")

    def _source(
        self, request: AcquisitionRequest, config: Keithley2460Config
    ) -> tuple[SourceSchedule, float, tuple[float, ...], tuple[float, ...], float | None]:
        p, policy, caps = request.program, config.timing, self.capabilities
        steps: tuple[SourceStep, ...]
        repeats = 1
        rates: tuple[float, ...] = ()
        tones: tuple[float, ...] = ()
        source_period: float | None = None
        if isinstance(p, (PotentiostaticHold, GalvanostaticHold)):
            ticks = self._ticks(p.duration_s, policy.duration_tolerance_s, "hold duration")
            level = p.voltage if isinstance(p, PotentiostaticHold) else p.current_a
            steps = (SourceStep(level=level, dwell_ticks=ticks),)
            requested = p.duration_s
        elif isinstance(p, CurrentPulseSequence):
            period = self._ticks(p.period_s, policy.source_period_tolerance_s, "pulse period")
            width = self._ticks(p.pulse_width_s, policy.source_period_tolerance_s, "pulse width")
            rest = period - width
            if rest <= 0 or abs(
                rest * _decimal(caps.timer_resolution_s)
                - (_decimal(p.period_s) - _decimal(p.pulse_width_s))
            ) > _decimal(policy.source_period_tolerance_s):
                raise ValidationError("Pulse baseline dwell cannot meet timing tolerance")
            steps = (
                SourceStep(level=p.pulse_current_a, dwell_ticks=width),
                SourceStep(level=p.baseline_current_a, dwell_ticks=rest, segment_index=1),
            )
            repeats = p.count
            requested = number(float(_decimal(p.period_s) * repeats), "pulse duration")
        elif isinstance(p, CyclicVoltammetry):
            steps, requested, rates = self._cv(p, config)
            source_period = self._seconds(steps[0].dwell_ticks)
        else:
            length = len(p.levels) if isinstance(p, ArbitraryWaveform) else (1 << p.order) - 1
            self._point_budget(length, p.repeats)
            if isinstance(p, ArbitraryWaveform):
                levels, requested_period = p.levels, p.point_period_s
            else:
                bits = prbs_bits(
                    p.order, p.seed, generator_id=p.generator_id, max_points=caps.max_unique_points
                )
                levels = tuple(p.high_level if bit else p.low_level for bit in bits)
                requested_period = p.bit_period_s
            ticks = self._ticks(requested_period, policy.source_period_tolerance_s, "source period")
            steps = tuple(
                SourceStep(level=v, dwell_ticks=ticks, segment_index=i)
                for i, v in enumerate(levels)
            )
            repeats = p.repeats
            requested = number(
                float(_decimal(requested_period) * length * repeats), "waveform duration"
            )
            source_period = self._seconds(ticks)
            if isinstance(p, ArbitraryWaveform) and p.multisine_spec is not None:
                spec = p.multisine_spec
                tones = tuple(index / (length * source_period) for index in spec.tone_bins)
                for wanted, achieved in zip(spec.frequencies_hz, tones, strict=True):
                    residue = spec.coherence_tolerance_bins / (length * spec.point_period_s)
                    if abs(
                        wanted - achieved
                    ) > policy.frequency_tolerance_hz + residue + 8 * math.ulp(wanted):
                        raise ValidationError("Achieved multisine tone exceeds frequency tolerance")
        self._point_budget(len(steps), repeats)
        boundaries = (0, *accumulate(step.dwell_ticks for step in steps))
        return (
            SourceSchedule(steps=steps, repeats=repeats, boundaries_ticks=boundaries),
            requested,
            rates,
            tones,
            source_period,
        )

    def _cv(
        self, p: CyclicVoltammetry, config: Keithley2460Config
    ) -> tuple[tuple[SourceStep, ...], float, tuple[float, ...]]:
        policy = config.timing
        ticks = self._ticks(p.sample_period_s, policy.measurement_period_tolerance_s, "CV period")
        self._ticks(p.sample_period_s, policy.source_period_tolerance_s, "CV source period")
        dt = self._seconds(ticks)
        vertices = (p.start_voltage_v, p.first_vertex_v, p.second_vertex_v, p.start_voltage_v)
        intervals, rates = [], []
        scan_time = Decimal(0)
        for a, b in zip(vertices[:-1], vertices[1:], strict=True):
            distance = abs(_decimal(b) - _decimal(a))
            if distance == 0:
                intervals.append(0)
                rates.append(0.0)
                continue
            wanted_time = distance / _decimal(p.scan_rate_v_per_s)
            count = int((wanted_time / _decimal(dt)).to_integral_value(rounding=ROUND_CEILING))
            actual = float(distance / (count * _decimal(dt)))
            if abs(
                actual - p.scan_rate_v_per_s
            ) > policy.scan_rate_tolerance_v_per_s + 8 * math.ulp(p.scan_rate_v_per_s):
                raise ValidationError("Discrete CV rate exceeds scan_rate_tolerance_v_per_s")
            scan_time += wanted_time
            intervals.append(count)
            rates.append(actual)
        self._point_budget(sum(intervals) * p.cycles + 1, 1)
        steps = [SourceStep(level=p.start_voltage_v, dwell_ticks=ticks)]
        for cycle in range(p.cycles):
            for segment, (a, b, count) in enumerate(
                zip(vertices[:-1], vertices[1:], intervals, strict=True)
            ):
                for j in range(1, count + 1):
                    level = b if j == count else a + (b - a) * (j / count)
                    steps.append(
                        SourceStep(
                            level=level, dwell_ticks=ticks, cycle_index=cycle, segment_index=segment
                        )
                    )
        requested = number(float(scan_time * p.cycles + _decimal(p.sample_period_s)), "CV duration")
        return tuple(steps), requested, tuple(rates)

    def _validate_levels(
        self,
        source: SourceSchedule,
        profile: SourceCapabilities,
        config: Keithley2460Config,
        function: SourceFunction,
        compliance: float,
    ) -> None:
        caps = self.capabilities
        for step in source.steps:
            if self._seconds(step.dwell_ticks) < max(
                profile.min_dwell_s, caps.source_action_time_s
            ):
                raise ValidationError("Source dwell is below minimum/action time")
            self._level(step.level, profile, config, function, compliance)
        pairs = list(zip(source.steps[:-1], source.steps[1:], strict=True))
        if source.repeats > 1:
            pairs.append((source.steps[-1], source.steps[0]))
        for before, after in pairs:
            delta = abs(after.level - before.level)
            if (
                delta > profile.max_step
                or delta / self._seconds(before.dwell_ticks) > profile.max_command_slope_per_s
            ):
                raise ValidationError(
                    "Transition/repeat boundary exceeds step/command-slope policy"
                )

    @staticmethod
    def _level(
        level: float,
        profile: SourceCapabilities,
        config: Keithley2460Config,
        function: SourceFunction,
        compliance: float,
    ) -> None:
        safety = config.safety
        if not profile.level_min <= level <= profile.level_max:
            raise ValidationError("Source level is outside capability limits")
        if function == "voltage" and not safety.voltage_min_v <= level <= safety.voltage_max_v:
            raise ValidationError("Voltage source level is outside cell limits")
        if (
            function == "current"
            and not -safety.discharge_current_max_a <= level <= safety.charge_current_max_a
        ):
            raise ValidationError("Current source level is outside cell limits")
        if abs(level) * compliance > safety.power_abs_max_w:
            raise ValidationError("Worst-case source/compliance power exceeds cell limit")

    def _validate_multisine(
        self,
        p: ArbitraryWaveform,
        profile: SourceCapabilities,
        config: Keithley2460Config,
        function: SourceFunction,
        compliance: float,
    ) -> None:
        spec = p.multisine_spec
        assert spec is not None
        caps = self.capabilities
        if spec.point_count / max(spec.tone_bins) < caps.min_points_per_highest_tone:
            raise UnsupportedCapabilityError(
                "Multisine is under-resolved for profile fidelity policy"
            )
        expected = multisine_levels(
            spec, max_points=caps.max_unique_points, max_tones=caps.max_tones
        )
        amplitude = math.fsum(spec.amplitudes)
        tolerance = 8 * math.ulp(abs(spec.bias) + amplitude)
        if any(abs(a - b) > tolerance for a, b in zip(p.levels, expected, strict=True)):
            raise ValidationError("Retained multisine spec does not reproduce saved levels")
        for level in (spec.bias - amplitude, spec.bias + amplitude):
            self._level(level, profile, config, function, compliance)
