"""Construct intent only: this does not validate, connect, or energize hardware."""

from ophyd_electrochemistry import AcquisitionRequest, CyclicVoltammetry, StartMode

request = AcquisitionRequest(
    experiment_id="example-shared-experiment-id",
    start_mode=StartMode.EXTERNAL_TRIGGER,
    program=CyclicVoltammetry(
        start_voltage_v=3.0,
        first_vertex_v=4.2,
        second_vertex_v=2.8,
        scan_rate_v_per_s=1e-3,
        cycles=3,
        sample_period_s=1.0,
        current_limit_a=0.05,
    ),
)
