"""Find the statement that logs 5076 "Source Limit changed ... due to new measure Range".

B1 R1, 5 Nov: after a power cycle, the first upload with a voltage-source step
(R7's CV steps) logs 5076 and clamps the current limit to 1.06 mA, although the
runtime sets a 10 mA measure range. This replays the configuration statements of
that upload one at a time, output OFF throughout, without the runtime, trigger
model or config lists, and after each statement records the new event-log
entries and the source/measure state.

Run on a freshly power-cycled 2460 (the clamp shows only from power-on state).
No output, no trigger model; 100 ohm four-wire resistor on the front terminals.

    uv run --locked --extra visa python bench/limit_clamp_probe.py
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import pyvisa

RESOURCE = "TCPIP0::169.254.113.151::5025::SOCKET"
EXPECTED_SERIAL = "04686198"

MEASURE_V = [
    "smu.measure.func = smu.FUNC_DC_VOLTAGE",
    "smu.measure.sense = smu.SENSE_4WIRE",
    "smu.measure.range = 0.9",
    "smu.measure.autorange = smu.OFF",
    "smu.measure.nplc = 1",
    "smu.measure.autozero.enable = smu.OFF",
]
MEASURE_I = [
    "smu.measure.func = smu.FUNC_DC_CURRENT",
    "smu.measure.sense = smu.SENSE_4WIRE",
    "smu.measure.range = 0.005",
    "smu.measure.autorange = smu.OFF",
    "smu.measure.nplc = 1",
    "smu.measure.autozero.enable = smu.OFF",
]


def source_i(level: float) -> list[str]:
    return [
        "smu.source.func = smu.FUNC_DC_CURRENT",
        "if smu.source.vlimit.level > 0.9 then smu.source.vlimit.level = 0.9 end",
        *MEASURE_V,
        "smu.source.autorange = smu.OFF",
        "smu.source.range = 0.01",
        "smu.source.vlimit.level = 0.9",
        "smu.source.readback = smu.ON",
        f"smu.source.level = {level}",
    ]


def source_v(level: float) -> list[str]:
    return [
        "smu.source.func = smu.FUNC_DC_VOLTAGE",
        "if smu.source.ilimit.level > 0.005 then smu.source.ilimit.level = 0.005 end",
        *MEASURE_I,
        "smu.source.autorange = smu.OFF",
        "smu.source.range = 2",
        "smu.source.ilimit.level = 0.005",
        "smu.source.readback = smu.ON",
        f"smu.source.level = {level}",
    ]


# oe_seq_configure, then the R7 program's source and measure entries, then commit.
STATEMENTS = [
    "smu.terminals = smu.TERMINALS_FRONT",
    "smu.source.func = smu.FUNC_DC_VOLTAGE",
    "smu.source.offmode = smu.OFFMODE_HIGHZ",
    "smu.source.func = smu.FUNC_DC_CURRENT",
    "smu.source.offmode = smu.OFFMODE_HIGHZ",
    "smu.source.level = 0",
    *source_i(0.0),
    *source_v(0.3),
    *source_v(0.2),
    *source_i(-0.0049),
    "smu.source.func = smu.FUNC_DC_CURRENT",
    *[s.replace("0.9", "2") for s in MEASURE_V],
    "smu.source.func = smu.FUNC_DC_VOLTAGE",
    *[s.replace("0.005", "0.01") for s in MEASURE_I],
    "smu.source.func = smu.FUNC_DC_VOLTAGE",
    "smu.source.func = smu.FUNC_DC_CURRENT",
    "smu.source.level = 0",
    "smu.measure.func = smu.FUNC_DC_VOLTAGE",
]
STATE = (
    "smu.source.func",
    "smu.measure.func",
    "smu.measure.range",
    "smu.measure.autorange",
    "smu.source.ilimit.level",
    "smu.source.vlimit.level",
)


def main() -> int:
    k = pyvisa.ResourceManager("@py").open_resource(RESOURCE, open_timeout=5000)
    k.read_termination = k.write_termination = "\n"
    k.timeout = 5000

    def query(lua: str) -> str:
        return str(k.query(f"print({lua})")).strip()

    def events() -> list[str]:
        count = int(float(query("eventlog.getcount(eventlog.SEV_ALL)")))
        return [query("eventlog.next(eventlog.SEV_ALL)") for _ in range(count)]

    if EXPECTED_SERIAL not in query("localnode.serialno"):
        raise SystemExit("STOP: wrong instrument")
    if query("smu.source.output") != "smu.OFF":
        raise SystemExit("STOP: output is not OFF")
    log: list[dict[str, object]] = [{"stale_events": events()}]

    def state() -> dict[str, str]:
        return {s: query(s) for s in STATE}

    log.append({"power_on_state": state()})
    for statement in STATEMENTS:
        k.write(statement)
        new = events()
        entry: dict[str, object] = {"statement": statement, "state": state()}
        if new:
            entry["events"] = new
            print(f"EVENT after {statement!r}: {new}\n  state {entry['state']}")
        log.append(entry)
    if query("smu.source.output") != "smu.OFF":
        raise SystemExit("STOP: output is not OFF at the end")
    k.close()
    out = Path("docs/evidence") / f"k2460-{datetime.now(UTC):%Y-%m-%d}-limit-clamp-probe.json"
    out.write_text(json.dumps(log, indent=1) + "\n")
    print(f"{len(STATEMENTS)} statements, output OFF; {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
