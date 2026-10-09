"""Adapt independent M2 data into the hardware-neutral M5 record contract."""

import pytest

from ophyd_electrochemistry import (
    AcquisitionRequest,
    FieldOrigin,
    GalvanostaticHold,
    MappingQuality,
    StartMode,
    TimingOrigin,
)
from ophyd_electrochemistry.exceptions import AcquisitionAborted, ShutdownUnconfirmed


def request(mode=StartMode.IMMEDIATE):
    return AcquisitionRequest(
        experiment_id="m5-synthetic-adapter",
        start_mode=mode,
        program=GalvanostaticHold(
            current_a=0.01,
            duration_s=0.04,
            sample_period_s=0.01,
            voltage_limit_v=5,
        ),
    )


def test_terminal_simulation_adapts_without_claiming_hardware_origin(runtime):
    program = runtime.prepare(request(), acquisition_id="acq-m5")
    runtime.kickoff()
    runtime.advance_ticks(40)
    retained = runtime.retained_buffer()
    assert retained.schema.synthetic
    assert retained.schema.voltage_origin == retained.schema.current_origin == FieldOrigin.SYNTHETIC
    assert retained.schema.timing_origin == TimingOrigin.SYNTHETIC
    assert retained.request_sha256 == program.request_sha256
    assert retained.program_sha256 == program.program_sha256
    assert retained.metadata.expected_records == retained.metadata.retained_records == 4
    for raw, value in zip(runtime.records, retained.records, strict=True):
        assert value.sample_index == raw.sample_index
        assert value.voltage_v == raw.voltage_v and value.current_a == raw.current_a
        assert value.mapping_quality in (
            MappingQuality.SETTLED_SINGLE_DWELL,
            MappingQuality.UNSETTLED_SINGLE_DWELL,
            MappingQuality.CROSSES_TRANSITION,
        )


def test_offset_chunk_retry_survives_lost_transport_reply(link, runtime):
    link.prepare(request(), acquisition_id="acq-m5")
    link.kickoff()
    runtime.advance_ticks(40)
    expected = link.read_record_chunk(offset=0, max_records=2)
    link.fail_next_transaction()
    with pytest.raises(ShutdownUnconfirmed):
        link.read_record_chunk(offset=2, max_records=2)
    link.connect()
    retried = link.read_record_chunk(offset=2, max_records=2)
    assert [record.sample_index for record in expected.records] == [0, 1]
    assert [record.sample_index for record in retried.records] == [2, 3]
    assert retried == link.read_record_chunk(offset=2, max_records=2)


def test_no_start_timeout_has_empty_frozen_buffer_without_fake_start(runtime):
    runtime.prepare(request(StartMode.EXTERNAL_TRIGGER), acquisition_id="acq-no-start")
    runtime.kickoff()
    runtime.advance_ticks(30)
    with pytest.raises(AcquisitionAborted):
        runtime.completion()
    retained = runtime.retained_buffer()
    assert retained.records == ()
    assert retained.schema.instrument_start_timestamp_s is None
    assert retained.metadata.terminal_cause == "start_timeout"
    assert not retained.metadata.waveform_complete
