"""Output-OFF firmware feature probe for the sequencer design (bench test B0).

Answers design questions from docs/mvp-plan.md without energizing the output:

P1  current output-off mode, output state, trigger-model state (query only)
P2  HIGH-Z output-off mode can be set and read back
P3  a source configuration list can hold both current and voltage functions
P4  BRANCH_ON_EVENT latch semantics (the suspected v17 failure cause)
      A: event consumed by WAIT between two passes of the same branch block
      B: event occurring before a branch block is first reached
P5  largest reading buffer that can be allocated (allocated, then deleted)

Preconditions (checked): identity/serial, TSP mode, output OFF, trigger model
not running. The output is never turned on. Touches only: offmode (left at
HIGH-Z), source function/level (restored to current, 0 A), a temporary config
list and buffer (deleted), timer 1 and the trigger model (left EMPTY). The
runtime's retained buffer and defbuffer1/2 are not touched.

Usage:  uv run python bench/feature_probe.py --i-confirm-output-off
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from ophyd_electrochemistry.keithley.k2460.transport import (
    PyVisaKeithley2460Transport,
    VisaTransportConfig,
)

RESOURCE = "TCPIP0::169.254.113.151::5025::SOCKET"
EXPECTED_SERIAL = "04686198"
PARK = "trigger.EVENT_NOTIFY8"  # never generated: parks the model at a known block

log: list[dict[str, str]] = []


def q(t: PyVisaKeithley2460Transport, command: str) -> str:
    reply = t.query(command)
    log.append({"query": command, "reply": reply})
    print(f"  {command}\n    -> {reply}")
    return reply


def w(t: PyVisaKeithley2460Transport, command: str) -> None:
    t.write(command)
    log.append({"write": command})


def guarded(lua: str) -> str:
    """Run Lua under pcall and print 'OK <result>' or 'ERR <message>'."""
    return (
        f"local ok, r = pcall(function() {lua} end) "
        "print(ok and ('OK ' .. tostring(r)) or ('ERR ' .. tostring(r)))"
    )


def require_idle_off(t: PyVisaKeithley2460Transport) -> None:
    if q(t, "print(smu.source.output == smu.OFF)") != "true":
        raise SystemExit("STOP: output is not OFF")
    state = q(t, "print(trigger.model.state())")
    if "RUNNING" in state or "WAITING" in state:
        raise SystemExit("STOP: trigger model is active")


def setup_timer1(t: PyVisaKeithley2460Transport, delay_s: float) -> None:
    w(t, "trigger.timer[1].reset()")
    w(t, f"trigger.timer[1].delay = {delay_s}")
    w(t, "trigger.timer[1].count = 1")
    w(t, "trigger.timer[1].start.stimulus = trigger.EVENT_NOTIFY1")
    w(t, "trigger.timer[1].start.generate = trigger.OFF")
    w(t, "trigger.timer[1].enable = trigger.ON")


def run_parked_model(t: PyVisaKeithley2460Transport, blocks: list[str], wait_s: float) -> str:
    w(t, 'trigger.model.load("Empty")')
    for index, args in enumerate(blocks, start=1):
        w(t, f"trigger.model.setblock({index}, {args})")
    w(t, "trigger.model.initiate()")
    time.sleep(wait_s)
    state = q(t, "print(trigger.model.state())")
    w(t, "trigger.model.abort()")
    time.sleep(0.2)
    q(t, "print(trigger.model.state())")
    return state


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resource", default=RESOURCE)
    parser.add_argument("--i-confirm-output-off", action="store_true", required=True)
    args = parser.parse_args()
    results: dict[str, str] = {}

    config = VisaTransportConfig(
        resource_name=args.resource, backend="@py", required_language="TSP", io_timeout_ms=5_000
    )
    t = PyVisaKeithley2460Transport(config)
    t.connect()
    try:
        identity = t.identity
        print(f"Connected: {identity}")
        if identity.serial_number != EXPECTED_SERIAL:
            raise SystemExit(f"STOP: unexpected serial {identity.serial_number}")

        print("P1 query-only state")
        require_idle_off(t)
        results["P1_offmode"] = q(t, "print(smu.source.offmode)")
        results["P1_func"] = q(t, "print(smu.source.func)")
        results["P1_event_count"] = q(t, "print(eventlog.getcount(eventlog.SEV_ALL))")

        print("P2 HIGH-Z off mode")
        results["P2_set_highz"] = q(
            t, guarded("smu.source.offmode = smu.OFFMODE_HIGHZ return smu.source.offmode")
        )

        print("P3 config list with mixed source functions")
        results["P3_configlist"] = q(
            t,
            guarded(
                'smu.source.configlist.create("OE_PROBE") '
                "smu.source.func = smu.FUNC_DC_CURRENT smu.source.level = 0 "
                'smu.source.configlist.store("OE_PROBE") '
                "smu.source.func = smu.FUNC_DC_VOLTAGE smu.source.level = 0 "
                'smu.source.configlist.store("OE_PROBE") '
                'smu.source.configlist.recall("OE_PROBE", 1) local a = tostring(smu.source.func) '
                'smu.source.configlist.recall("OE_PROBE", 2) local b = tostring(smu.source.func) '
                'return a .. " / " .. b'
            ),
        )
        q(t, guarded('smu.source.configlist.delete("OE_PROBE") return 1'))
        q(
            t,
            guarded(
                "smu.source.func = smu.FUNC_DC_CURRENT smu.source.level = 0 return smu.source.func"
            ),
        )
        require_idle_off(t)

        print("P4A latch: event consumed by WAIT between two passes of one branch block")
        setup_timer1(t, 0.05)
        results["P4A_state"] = run_parked_model(
            t,
            [
                "trigger.BLOCK_NOTIFY, trigger.EVENT_NOTIFY1",  # 1 start 50 ms timer
                "trigger.BLOCK_BRANCH_ON_EVENT, trigger.EVENT_TIMER1, 7",  # 2 pass 1 at t~0
                "trigger.BLOCK_WAIT, trigger.EVENT_TIMER1, trigger.CLEAR_NEVER",  # 3 consumes
                "trigger.BLOCK_DELAY_CONSTANT, 0.1",  # 4
                "trigger.BLOCK_BRANCH_COUNTER, 1, 2",  # 5 back to block 2 once
                f"trigger.BLOCK_WAIT, {PARK}",  # 6 park: fell through
                f"trigger.BLOCK_WAIT, {PARK}",  # 7 park: branch taken
            ],
            wait_s=1.0,
        )
        print("    interpret: parked at 3 = not latched (waits for new event); 7 = latched")

        print("P4B latch: event occurs before the branch block is first reached")
        setup_timer1(t, 0.05)
        results["P4B_state"] = run_parked_model(
            t,
            [
                "trigger.BLOCK_NOTIFY, trigger.EVENT_NOTIFY1",  # 1 start 50 ms timer
                "trigger.BLOCK_DELAY_CONSTANT, 0.1",  # 2
                "trigger.BLOCK_BRANCH_ON_EVENT, trigger.EVENT_TIMER1, 5",  # 3 first reach
                f"trigger.BLOCK_WAIT, {PARK}",  # 4 park: not seen
                f"trigger.BLOCK_WAIT, {PARK}",  # 5 park: seen
            ],
            wait_s=0.6,
        )
        print("    interpret: parked at 4 = not seen; 5 = seen")
        w(t, "trigger.timer[1].enable = trigger.OFF")
        w(t, "trigger.timer[1].reset()")
        w(t, 'trigger.model.load("Empty")')

        print("P5 largest allocatable reading buffer")
        for size in (1_000_000, 500_000, 250_000):
            reply = q(
                t,
                guarded(
                    f'local b = buffer.make("oe_probe", {size}) '
                    "local c = b.capacity buffer.delete(b) return c"
                ),
            )
            results[f"P5_{size}"] = reply
            if reply.startswith("OK"):
                break

        print("Final state")
        require_idle_off(t)
        results["final_offmode"] = q(t, "print(smu.source.offmode)")
        results["final_model"] = q(t, "print(trigger.model.state())")
        results["final_event_count"] = q(t, "print(eventlog.getcount(eventlog.SEV_ALL))")
    finally:
        t.close()

    stamp = datetime.now(UTC)
    out = Path("docs/evidence") / f"k2460-{stamp:%Y-%m-%d}-feature-probe.json"
    out.write_text(
        json.dumps(
            {
                "schema": "ophyd-electrochemistry.bench-probe.v1",
                "observed_at_utc": stamp.isoformat(),
                "resource": args.resource,
                "results": results,
                "transcript": log,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
