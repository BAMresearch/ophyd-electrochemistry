"""Energized function-switch probe E1 (tier 1: 100 ohm resistor, four-wire only).

Question for the sequencer design: does a source + measure config-list recall
inside a running trigger model switch current -> voltage -> current source
while the output stays ON, with both V and I recorded? (B0/P7 showed the
switch with output OFF.)

One trigger model, output on for about 2.5 s, 20 readings per phase at 1 NPLC:

  phase 1  current source +4 mA (vlimit 1 V), measure V   expect ~0.40 V
  phase 2  voltage source 0.30 V (ilimit 5 mA), measure I  expect ~3.0 mA
  phase 3  current source 0 A (vlimit 1 V), measure V      expect ~0 V

Digital line 2 (the runtime's "busy" line, active high) is asserted from the
first block until after the output-off block. Off mode HIGH-Z is set and
verified for both source functions before arming. Fixed ranges: 10 mA, 2 V.
Limits authorized by the user on 2026-11-03: |I| <= 5 mA on the 100 ohm
four-wire resistor. The output is turned OFF and verified in a finally block,
and after a 10 s watchdog. TSP is Lua 5.0.2.

Usage:  uv run --locked --extra visa python bench/function_switch_probe.py \\
            --i-confirm-100-ohm-four-wire-resistor
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from feature_probe import (
    EXPECTED_SERIAL,
    RESOURCE,
    drain_stale_output,
    guarded,
    log,
    q,
    require_idle_off,
    w,
)

from ophyd_electrochemistry.keithley.k2460.transport import (
    PyVisaKeithley2460Transport,
    VisaTransportConfig,
)

COUNT = 20  # readings per phase
WATCHDOG_S = 10.0

# The instrument rejects long input lines ("Input buffer overrun" at ~3 KB), so
# the setup is sent in short chunks, each under pcall.
_MEASURE_V = (
    "smu.measure.func = smu.FUNC_DC_VOLTAGE smu.measure.sense = smu.SENSE_4WIRE "
    "smu.measure.autorange = smu.OFF smu.measure.range = 2 smu.measure.nplc = 1 "
)
_SOURCE_I = (
    "smu.source.func = smu.FUNC_DC_CURRENT smu.source.autorange = smu.OFF "
    "smu.source.range = 0.01 smu.source.vlimit.level = 1 smu.source.readback = smu.ON "
)
_STORE = 'smu.source.configlist.store("OE_SRC") smu.measure.configlist.store("OE_MEAS") '
SETUP_PARTS = (
    # Off mode is stored per source function: HIGH-Z for both.
    "smu.source.func = smu.FUNC_DC_CURRENT smu.source.offmode = smu.OFFMODE_HIGHZ "
    "smu.source.func = smu.FUNC_DC_VOLTAGE smu.source.offmode = smu.OFFMODE_HIGHZ "
    "digio.line[2].mode = digio.MODE_DIGITAL_OUT digio.line[2].state = 0 "
    'smu.source.configlist.create("OE_SRC") smu.measure.configlist.create("OE_MEAS") '
    "oe_lists = true return 1",
    # Entry 1: CC +4 mA, measure V.
    _MEASURE_V + _SOURCE_I + "smu.source.level = 0.004 " + _STORE + "return 1",
    # Entry 2: CV 0.30 V, measure I.
    "smu.measure.func = smu.FUNC_DC_CURRENT smu.measure.sense = smu.SENSE_4WIRE "
    "smu.measure.autorange = smu.OFF smu.measure.range = 0.01 smu.measure.nplc = 1 "
    "smu.source.func = smu.FUNC_DC_VOLTAGE smu.source.autorange = smu.OFF "
    "smu.source.range = 2 smu.source.ilimit.level = 0.005 smu.source.readback = smu.ON "
    "smu.source.level = 0.3 " + _STORE + "return 1",
    # Entry 3: CC 0 A, measure V. Also left as the active (safe) settings.
    _MEASURE_V + _SOURCE_I + "smu.source.level = 0 " + _STORE + "return 1",
    f"oe_e1 = buffer.make({3 * COUNT + 10}, buffer.STYLE_STANDARD) "
    'trigger.model.load("Empty") '
    "trigger.model.setblock(1, trigger.BLOCK_DIGITAL_IO, 2, 2) "
    'trigger.model.setblock(2, trigger.BLOCK_CONFIG_RECALL, "OE_SRC", 1, "OE_MEAS", 1) '
    "trigger.model.setblock(3, trigger.BLOCK_SOURCE_OUTPUT, smu.ON) "
    f"trigger.model.setblock(4, trigger.BLOCK_MEASURE_DIGITIZE, oe_e1, {COUNT}) "
    'trigger.model.setblock(5, trigger.BLOCK_CONFIG_RECALL, "OE_SRC", 2, "OE_MEAS", 2) '
    "return 1",
    f"trigger.model.setblock(6, trigger.BLOCK_MEASURE_DIGITIZE, oe_e1, {COUNT}) "
    'trigger.model.setblock(7, trigger.BLOCK_CONFIG_RECALL, "OE_SRC", 3, "OE_MEAS", 3) '
    f"trigger.model.setblock(8, trigger.BLOCK_MEASURE_DIGITIZE, oe_e1, {COUNT}) "
    "trigger.model.setblock(9, trigger.BLOCK_SOURCE_OUTPUT, smu.OFF) "
    "trigger.model.setblock(10, trigger.BLOCK_DIGITAL_IO, 0, 2) "
    "return 1",
)
assert all(len(guarded(part)) < 900 for part in SETUP_PARTS)

READOUT = (
    "local r = '' for i = 1, oe_e1.n do "
    "r = r .. string.format('%.6f', oe_e1.relativetimestamps[i]) .. ',' "
    ".. string.format('%.7g', oe_e1.readings[i]) .. ',' .. tostring(oe_e1.units[i]) .. ',' "
    ".. string.format('%.7g', oe_e1.sourcevalues[i]) .. ',' .. tostring(oe_e1.sourceunits[i]) "
    ".. ';' end return r"
)
STATUSES = (
    "local r = '' for i = 1, oe_e1.n do "
    "r = r .. tostring(oe_e1.statuses[i]) .. '/' .. tostring(oe_e1.sourcestatuses[i]) .. ' ' "
    "end return r"
)


def shutdown(t: PyVisaKeithley2460Transport) -> str:
    """Abort, force output OFF, drop the busy line; return the verified output state."""
    w(t, "trigger.model.abort()")
    w(t, "smu.source.output = smu.OFF")
    time.sleep(0.2)
    q(t, guarded("digio.line[2].state = 0 return digio.line[2].state"))
    return q(t, "print(smu.source.output)")


def emergency_off(resource: str) -> str:
    """Abort and force output OFF over a fresh raw session; return the output state."""
    import pyvisa

    session = pyvisa.ResourceManager("@py").open_resource(resource, open_timeout=5000)
    try:
        session.read_termination = session.write_termination = "\n"
        session.timeout = 3000
        session.write("trigger.model.abort()")
        session.write("smu.source.output = smu.OFF")
        session.write('print("OE-OFF")')
        while session.read() != "OE-OFF":
            pass
        state = session.query("print(smu.source.output)")
        print(f"EMERGENCY OFF over a fresh session: {state}")
        return state
    finally:
        session.close()


def cleanup(t: PyVisaKeithley2460Transport) -> None:
    q(
        t,
        guarded(
            "if oe_e1 ~= nil then buffer.delete(oe_e1) oe_e1 = nil end "
            "if oe_lists then smu.source.configlist.delete('OE_SRC') "
            "smu.measure.configlist.delete('OE_MEAS') oe_lists = nil end "
            "smu.source.func = smu.FUNC_DC_CURRENT smu.source.level = 0 "
            'trigger.model.load("Empty") return smu.source.output'
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resource", default=RESOURCE)
    parser.add_argument(
        "--i-confirm-100-ohm-four-wire-resistor", action="store_true", required=True
    )
    args = parser.parse_args()
    results: dict[str, str] = {}

    drain_stale_output(args.resource)
    config = VisaTransportConfig(
        resource_name=args.resource, backend="@py", required_language="TSP", io_timeout_ms=5_000
    )
    t = PyVisaKeithley2460Transport(config)
    t.connect()
    try:
        if t.identity.serial_number != EXPECTED_SERIAL:
            raise SystemExit(f"STOP: unexpected serial {t.identity.serial_number}")
        require_idle_off(t)
        results["pre_terminals"] = q(t, "print(smu.terminals)")
        if results["pre_terminals"] != "smu.TERMINALS_FRONT":
            raise SystemExit("STOP: resistor is expected on the front terminals")
        results["pre_events"] = q(t, "print(eventlog.getcount(eventlog.SEV_ALL))")
        cleanup(t)  # Leftovers of an interrupted earlier run.

        print("Setup (output OFF)")
        for index, part in enumerate(SETUP_PARTS, start=1):
            results[f"setup_{index}"] = q(t, guarded(part))
            if not results[f"setup_{index}"].startswith("OK"):
                raise SystemExit(f"STOP: setup step {index} failed before arming")
        results["offmode_both"] = q(
            t,
            guarded(
                "smu.source.func = smu.FUNC_DC_VOLTAGE local v = smu.source.offmode "
                "smu.source.func = smu.FUNC_DC_CURRENT local i = smu.source.offmode "
                "return tostring(i) .. ' / ' .. tostring(v)"
            ),
        )
        if results["offmode_both"] != "OK smu.OFFMODE_HIGHZ / smu.OFFMODE_HIGHZ":
            raise SystemExit("STOP: HIGH-Z not confirmed for both functions")
        require_idle_off(t)

        print("Run (output ON for about 2.5 s)")
        w(t, "trigger.model.initiate()")
        started = time.monotonic()
        while True:
            time.sleep(0.2)
            state = q(t, "print(trigger.model.state())")
            if "RUNNING" not in state and "WAITING" not in state:
                break
            if time.monotonic() - started > WATCHDOG_S:
                results["watchdog"] = f"model still active after {WATCHDOG_S} s: {state}"
                break
        results["elapsed_s"] = f"{time.monotonic() - started:.2f}"
        results["model_state"] = state
        results["output_after_model"] = q(t, "print(smu.source.output)")
        results["busy_after_model"] = q(t, "print(digio.line[2].state)")
        results["readout"] = q(t, guarded(READOUT))
        results["statuses"] = q(t, guarded(STATUSES))
    finally:
        try:
            results["output_final"] = shutdown(t)
        except Exception as exc:
            # A timed-out query invalidates the session: shut down over a fresh one.
            results["shutdown_error"] = repr(exc)
            results["output_final"] = emergency_off(args.resource)
            raise
        try:
            cleanup(t)
            results["final_events"] = q(t, "print(eventlog.getcount(eventlog.SEV_ALL))")
            count = int(float(results["final_events"]))
            results["event_log"] = " | ".join(
                q(t, "print(eventlog.next(eventlog.SEV_ALL))") for _ in range(count)
            )
        finally:
            t.close()

    print(f"OUTPUT FINAL: {results.get('output_final')}")
    for phase, rows in summarize(results.get("readout", "")).items():
        print(f"  {phase}: {rows}")
    stamp = datetime.now(UTC)
    out = Path("docs/evidence") / f"k2460-{stamp:%Y-%m-%d}-function-switch-e1.json"
    out.write_text(
        json.dumps(
            {
                "schema": "ophyd-electrochemistry.bench-probe.v1",
                "observed_at_utc": stamp.isoformat(),
                "resource": args.resource,
                "load": "100 ohm 1 W resistor, four-wire, front terminals (user-confirmed)",
                "authorization": "user 2026-11-03: |I| <= 5 mA, busy line 2 as marker",
                "results": results,
                "transcript": log,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"Saved {out}")


def summarize(readout: str) -> dict[str, str]:
    """Per-phase mean of reading and source value, from 'OK t,v,u,s,su;...'."""
    rows = [row.split(",") for row in readout.removeprefix("OK ").split(";") if row.count(",") == 4]
    phases = {}
    for index in range(3):
        chunk = rows[index * COUNT : (index + 1) * COUNT]
        if not chunk:
            continue
        mean = sum(float(row[1]) for row in chunk) / len(chunk)
        source = sum(float(row[3]) for row in chunk) / len(chunk)
        phases[f"phase {index + 1}"] = (
            f"{len(chunk)} readings, mean {mean:.6g} {chunk[0][2]}, "
            f"source {source:.6g} {chunk[0][4]}, first {chunk[0][1]}, t0 {chunk[0][0]} s"
        )
    return phases


if __name__ == "__main__":
    main()
