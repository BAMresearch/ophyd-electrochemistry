"""Query-only probe of the 2460 output-off mode (bench, any load).

Prints the source function, its output-off mode, the output state and the
trigger-model state. It never writes a setting: the off mode is stored per
source function, so only the currently selected function's mode is read.
`environment open` (and every prepare) sets HIGH-Z for both functions and the
runtime reads both back; this probe is an independent check of one of them.

Usage:  uv run --locked --extra visa python bench/offmode_probe.py [--label after-open]
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime

from feature_probe import drain_stale_output

from ophyd_electrochemistry.keithley.k2460.transport import (
    PyVisaKeithley2460Transport,
    VisaTransportConfig,
)

RESOURCE = "TCPIP0::169.254.113.151::5025::SOCKET"
EXPECTED_SERIAL = "04686198"
QUERIES = {
    "source_func": "print(smu.source.func)",
    "offmode": "print(smu.source.offmode)",
    "output": "print(smu.source.output)",
    "model_state": "print(trigger.model.state())",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resource", default=RESOURCE)
    parser.add_argument("--label", default="")
    args = parser.parse_args()
    config = VisaTransportConfig(
        resource_name=args.resource, backend="@py", required_language="TSP", io_timeout_ms=5_000
    )
    drain_stale_output(args.resource)
    t = PyVisaKeithley2460Transport(config)
    t.connect()
    try:
        identity = t.identity
        if identity.serial_number != EXPECTED_SERIAL:
            raise SystemExit(f"STOP: unexpected serial {identity.serial_number}")
        result = {"utc": datetime.now(UTC).isoformat(), "label": args.label}
        result |= {name: t.query(command) for name, command in QUERIES.items()}
    finally:
        t.close()
    print(json.dumps(result))


if __name__ == "__main__":
    main()
