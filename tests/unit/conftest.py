"""Explicit synthetic capabilities: these are not Keithley specifications."""

import pytest

from ophyd_electrochemistry.keithley.k2460 import (
    Keithley2460Capabilities,
    Keithley2460Config,
    SafetyConfig,
    SourceCapabilities,
    TimingPolicy,
)


@pytest.fixture
def config():
    return Keithley2460Config(
        visa_resource="SIM::M1_ONLY",
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
            required_cutoff_latency_s=0.2,
        ),
    )


@pytest.fixture
def capabilities():
    return Keithley2460Capabilities(
        profile_id="synthetic-m1-v1",
        evidence_kind="simulation",
        sources=(
            SourceCapabilities(
                source_function="current",
                level_min=-1,
                level_max=1,
                compliance_min=0.01,
                compliance_max=5,
                min_dwell_s=0.003,
                max_step=0.2,
                max_command_slope_per_s=100,
            ),
            SourceCapabilities(
                source_function="voltage",
                level_min=-5,
                level_max=5,
                compliance_min=0.001,
                compliance_max=0.5,
                min_dwell_s=0.003,
                max_step=1,
                max_command_slope_per_s=1000,
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
