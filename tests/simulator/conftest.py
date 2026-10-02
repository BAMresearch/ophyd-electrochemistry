"""Synthetic simulator fixtures; never hardware capability defaults."""

from dataclasses import replace

import pytest

from examples.compile_program import capabilities as example_capabilities
from examples.compile_program import config as example_config
from ophyd_electrochemistry.keithley.k2460 import SourceCapabilities
from ophyd_electrochemistry.simulation import FakeCell, FakeTransport, SimulatedRuntime


@pytest.fixture
def capabilities():
    return replace(
        example_capabilities,
        profile_id="m2-test-synthetic-v1",
        supports_external_abort=True,
        sources=example_capabilities.sources
        + (
            SourceCapabilities(
                source_function="voltage",
                level_min=-5,
                level_max=5,
                compliance_min=0.001,
                compliance_max=0.2,
                min_dwell_s=0.003,
                max_step=1,
                max_command_slope_per_s=1000,
            ),
        ),
    )


@pytest.fixture
def config():
    return replace(
        example_config,
        io=replace(example_config.io, external_abort_enabled=True),
        timing=replace(example_config.timing, external_start_timeout_s=0.03),
    )


@pytest.fixture
def runtime(capabilities, config):
    return SimulatedRuntime(
        capabilities=capabilities,
        config=config,
        cell=FakeCell(resistance_ohm=10, open_circuit_voltage_v=0.1),
    )


@pytest.fixture
def link(runtime):
    result = FakeTransport(runtime)
    result.connect()
    return result
