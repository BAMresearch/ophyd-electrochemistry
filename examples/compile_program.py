"""Run with uv run --locked python -m examples.compile_program.

All limits/timings below are synthetic planning fixtures, not battery defaults
or 2460 specifications. This example never connects to an instrument.
"""

import json

from ophyd_electrochemistry import AcquisitionRequest, PRBSWaveform, StartMode
from ophyd_electrochemistry.keithley.k2460 import (
    Keithley2460Capabilities,
    Keithley2460Compiler,
    Keithley2460Config,
    SafetyConfig,
    SourceCapabilities,
    TimingPolicy,
)

capabilities = Keithley2460Capabilities(
    profile_id="example-synthetic-v1",
    evidence_kind="simulation",
    sources=(
        SourceCapabilities(
            source_function="current",
            level_min=-0.2,
            level_max=0.2,
            compliance_min=0.01,
            compliance_max=5,
            min_dwell_s=0.003,
            max_step=0.1,
            max_command_slope_per_s=10,
        ),
    ),
    timer_resolution_s=0.001,
    source_action_time_s=0.001,
    source_settling_time_s=0.001,
    measurement_aperture_s=0.002,
    measurement_overhead_s=0.001,
    min_measurement_period_s=0.003,
    max_duration_s=100,
    max_unique_points=2000,
    max_logical_points=10_000,
    max_measurement_records=10_000,
    max_buffer_records=20_000,
    source_records_per_point=1,
    max_upload_bytes=2_000_000,
    max_trigger_blocks=5000,
    fixed_trigger_blocks=12,
    trigger_blocks_per_point=2,
    max_tones=64,
    min_points_per_highest_tone=8,
    supported_off_modes=("simulation-off",),
    supported_terminals=("rear",),
    supported_sense_modes=("local",),
    supports_external_start=True,
    supports_external_abort=False,
    supports_local_cutoffs=True,
    local_cutoffs_use_measurements=True,
    supports_independent_measurement=True,
    aperture_may_cross_updates=True,
    abort_latency_s=0.01,
    cutoff_latency_s=0.01,
)
config = Keithley2460Config(
    visa_resource="SIM::NO_CONNECTION",
    source_terminal="rear",
    sense="local",
    safety=SafetyConfig(
        voltage_min_v=-5,
        voltage_max_v=5,
        charge_current_max_a=0.2,
        discharge_current_max_a=0.2,
        power_abs_max_w=1,
        source_off_mode="simulation-off",
    ),
    timing=TimingPolicy(
        command_timeout_s=1,
        external_start_timeout_s=10,
        shutdown_timeout_s=1,
        poll_period_s=0.1,
        required_abort_latency_s=0.02,
        required_cutoff_latency_s=0.1,
    ),
)
request = AcquisitionRequest(
    program=PRBSWaveform(
        source_function="current",
        voltage_limit_v=5,
        low_level=-0.01,
        high_level=0.01,
        bit_period_s=0.01,
        sample_period_s=0.02,
        order=7,
        seed=1,
        repeats=10,
    ),
    experiment_id="offline-PRBS-example",
    start_mode=StartMode.EXTERNAL_TRIGGER,
)


def main() -> None:
    result = Keithley2460Compiler(capabilities).compile(request, config)
    print(
        json.dumps(
            {
                "hardware_ready": result.hardware_ready,
                "profile": result.capability_profile_id,
                "duration_s": result.achieved_duration_s,
                "source_points": result.budget.logical_points,
                "measurement_records": result.budget.measurement_records,
                "commanded_charge_c": result.commanded_charge_c,
                "request_sha256": result.request_sha256,
                "program_sha256": result.program_sha256,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
