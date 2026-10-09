"""Offline M2/M5 example: uv run --locked python -m examples.simulate_program."""

import json

from examples.compile_program import capabilities, config, request
from ophyd_electrochemistry.simulation import FakeCell, FakeTransport, SimulatedRuntime


def main() -> None:
    runtime = SimulatedRuntime(
        capabilities=capabilities,
        config=config,
        cell=FakeCell(resistance_ohm=10, open_circuit_voltage_v=0.1),
    )
    link = FakeTransport(runtime)
    link.connect()
    compiled = link.prepare(request, acquisition_id="offline-m2-acquisition")
    link.kickoff()
    assert runtime.ready and runtime.output is False
    runtime.advance_ticks(15)  # Waiting does not consume waveform time.
    runtime.set_inputs(start=True, abort=False)
    outcome = link.poll_until_terminal(
        max_ticks=compiled.source.duration_ticks,
        poll_ticks=137,
    )
    retained = link.retained_buffer()
    chunk = link.read_record_chunk(offset=0, max_records=1000)
    records = chunk.records
    print(
        json.dumps(
            {
                "synthetic": True,
                "hardware_ready": compiled.hardware_ready,
                "acquisition_id": outcome.acquisition_id,
                "success": outcome.success,
                "output_off_confirmed": outcome.output_off_confirmed,
                "measurement_schema": retained.schema.schema,
                "electrical_origin": retained.schema.voltage_origin.value,
                "start_tick": outcome.start_tick,
                "terminal_tick": outcome.terminal_tick,
                "source_points_executed": outcome.source_points_executed,
                "records": len(records),
                "records_sha256": retained.metadata.records_sha256,
                "chunk_final": chunk.final,
                "first_aperture_s": [
                    records[0].aperture_start_relative_s,
                    records[0].aperture_end_relative_s,
                ],
                "first_synthetic_current_a": records[0].current_a,
            },
            indent=2,
        )
    )
    # Collection does not discard. Explicit disposition follows review/archive.
    link.close()


if __name__ == "__main__":
    main()
