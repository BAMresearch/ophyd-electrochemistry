"""Output-OFF firmware feature probe for the sequencer design (bench test B0).

Answers design questions from docs/mvp-plan.md without energizing the output:

P1  current output-off mode, output state, trigger-model state (query only)
P2  HIGH-Z output-off mode can be set and read back
P3  a source configuration list can hold both current and voltage functions
P4  BRANCH_ON_EVENT latch semantics (the suspected v17 failure cause)
      A: event consumed by WAIT between two passes of the same branch block
      B: event occurring before a branch block is first reached
P5  largest reading buffer that can be allocated (allocated, then deleted)
P6  the reading buffer can be read in chunks while a trigger model is still
      measuring into it (live reads during a run), measured with output OFF
P7  one trigger model switches source AND measure function via config lists
      (BLOCK_CONFIG_RECALL), as a CC step followed by a CV step needs

Preconditions (checked): identity/serial, TSP mode, output OFF, trigger model
not running. The output is never turned on. Touches only: offmode (left at
HIGH-Z), source function/level (restored to current, 0 A), measure function
and NPLC (set to voltage, 1 NPLC; the runtime sets its own at prepare),
temporary config lists and buffers (deleted), timer 1 and the trigger model
(left EMPTY). The runtime's retained buffer and defbuffer1/2 are not touched.
Every probe runs under Lua pcall, so an unsupported command is a result, not
an abort; cleanup always aborts the model and deletes temporaries.

Run from the repository root (the evidence file goes to docs/evidence/).

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


def drain_stale_output(resource: str) -> None:
    """Read and discard reply lines an interrupted session left unread."""
    import pyvisa

    session = pyvisa.ResourceManager("@py").open_resource(resource, open_timeout=5000)
    try:
        session.read_termination = session.write_termination = "\n"
        session.timeout = 3000
        session.write('print("OE-SYNC")')
        stale = []
        while (line := session.read()) != "OE-SYNC":
            stale.append(line)
        log.append({"drained_stale_lines": str(len(stale))})
        print(f"Drained {len(stale)} stale reply line(s)")
    finally:
        session.close()


def cleanup(t: PyVisaKeithley2460Transport, buffer_name: str) -> None:
    """Stop the model, delete temporaries, restore current source at 0 A."""
    w(t, "trigger.model.abort()")
    time.sleep(0.2)
    q(
        t,
        guarded(
            f"if {buffer_name} ~= nil then buffer.delete({buffer_name}) {buffer_name} = nil end "
            "for _, name in ipairs({'OE_SRC', 'OE_MEAS'}) do "
            "pcall(smu.source.configlist.delete, name) "
            "pcall(smu.measure.configlist.delete, name) end "
            "smu.source.func = smu.FUNC_DC_CURRENT smu.source.level = 0 "
            "smu.measure.func = smu.FUNC_DC_VOLTAGE smu.measure.nplc = 1 "
            'trigger.model.load("Empty") return smu.source.output'
        ),
    )


def probe_live_reads(t: PyVisaKeithley2460Transport) -> dict[str, str]:
    """Measure 2000 readings at 0.01 NPLC; read new records every 0.25 s meanwhile."""
    results = {
        "P6_setup": q(
            t,
            guarded(
                "smu.measure.func = smu.FUNC_DC_VOLTAGE smu.measure.nplc = 0.01 "
                "oe_live = buffer.make(2000, buffer.STYLE_STANDARD) "
                'trigger.model.load("Empty") '
                "trigger.model.setblock(1, trigger.BLOCK_MEASURE_DIGITIZE, oe_live, 2000) "
                "return oe_live.capacity"
            ),
        )
    }
    if not results["P6_setup"].startswith("OK"):
        return results
    w(t, "trigger.model.initiate()")
    reads, read_while_running, last, started = [], 0, 0, time.monotonic()
    while time.monotonic() - started < 30:
        time.sleep(0.25)
        state = q(t, "print(trigger.model.state())")
        count = int(float(q(t, "print(oe_live.n)")))
        if count > last:
            # Fetch only the new records, as a live plot would.
            # One reply line per query: printbuffer would add a second one.
            reply = q(
                t,
                guarded(
                    f"local r = {{}} for i = {last + 1}, {count} do "
                    "r[#r + 1] = string.format('%.6f', oe_live.relativetimestamps[i]) end "
                    "return #r .. ' ' .. r[1] .. '..' .. r[#r]"
                ),
            )
            reads.append(f"{time.monotonic() - started:.2f}s n={count} state={state} {reply}")
            if "RUNNING" in state and reply.startswith("OK"):
                read_while_running += 1
            last = count
        if "RUNNING" not in state and "WAITING" not in state:
            break
    results["P6_reads"] = " | ".join(reads)
    results["P6_reads_while_running"] = str(read_while_running)
    results["P6_final_n"] = str(last)
    print(f"    interpret: reads while running = {read_while_running} (>0 means live reads work)")
    return results


def probe_function_switch(t: PyVisaKeithley2460Transport) -> dict[str, str]:
    """Two config-list entries: (I source, V measure) then (V source, I measure)."""
    results = {
        "P7_lists": q(
            t,
            guarded(
                'smu.source.configlist.create("OE_SRC") smu.measure.configlist.create("OE_MEAS") '
                "smu.source.func = smu.FUNC_DC_CURRENT smu.source.level = 0 "
                "smu.measure.func = smu.FUNC_DC_VOLTAGE smu.measure.nplc = 1 "
                'smu.source.configlist.store("OE_SRC") smu.measure.configlist.store("OE_MEAS") '
                "smu.source.func = smu.FUNC_DC_VOLTAGE smu.source.level = 0 "
                "smu.measure.func = smu.FUNC_DC_CURRENT smu.measure.nplc = 1 "
                'smu.source.configlist.store("OE_SRC") smu.measure.configlist.store("OE_MEAS") '
                "smu.source.func = smu.FUNC_DC_CURRENT smu.measure.func = smu.FUNC_DC_VOLTAGE "
                "return 1"
            ),
        )
    }
    if not results["P7_lists"].startswith("OK"):
        return results
    results["P7_list_sizes"] = q(
        t,
        guarded(
            'return smu.source.configlist.size("OE_SRC") .. " / " .. '
            'smu.measure.configlist.size("OE_MEAS")'
        ),
    )
    results["P7_model"] = q(
        t,
        guarded(
            "oe_switch = buffer.make(10, buffer.STYLE_STANDARD) "
            'trigger.model.load("Empty") '
            'trigger.model.setblock(1, trigger.BLOCK_CONFIG_RECALL, "OE_SRC", 1, "OE_MEAS", 1) '
            "trigger.model.setblock(2, trigger.BLOCK_MEASURE_DIGITIZE, oe_switch, 2) "
            'trigger.model.setblock(3, trigger.BLOCK_CONFIG_RECALL, "OE_SRC", 2, "OE_MEAS", 2) '
            "trigger.model.setblock(4, trigger.BLOCK_MEASURE_DIGITIZE, oe_switch, 2) "
            "return 1"
        ),
    )
    if not results["P7_model"].startswith("OK"):
        return results
    w(t, "trigger.model.initiate()")
    time.sleep(1.5)
    results["P7_state"] = q(t, "print(trigger.model.state())")
    results["P7_units"] = q(
        t,
        guarded(
            "local r = {} for i = 1, oe_switch.n do "
            "r[#r + 1] = tostring(oe_switch.units[i]) .. '/' .. tostring(oe_switch.sourceunits[i]) "
            "end return table.concat(r, ' ')"
        ),
    )
    results["P7_func_after"] = q(
        t, guarded('return tostring(smu.source.func) .. " / " .. tostring(smu.measure.func)')
    )
    print(
        "    interpret: units per reading measured/source. Volt/Amp twice then Amp/Volt"
        " twice = both functions switch inside one model"
    )
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resource", default=RESOURCE)
    parser.add_argument("--i-confirm-output-off", action="store_true", required=True)
    args = parser.parse_args()
    results: dict[str, str] = {}

    config = VisaTransportConfig(
        resource_name=args.resource, backend="@py", required_language="TSP", io_timeout_ms=5_000
    )
    drain_stale_output(args.resource)
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
        results["P1_defbuffers"] = q(t, "print(defbuffer1.n, defbuffer1.capacity, defbuffer2.n)")
        results["P1_continuous"] = q(t, "print(trigger.continuous)")

        print("P2 HIGH-Z off mode, per source function")
        results["P2_set_highz"] = q(
            t,
            guarded(
                "smu.source.func = smu.FUNC_DC_VOLTAGE smu.source.offmode = smu.OFFMODE_HIGHZ "
                "local a = tostring(smu.source.offmode) "
                "smu.source.func = smu.FUNC_DC_CURRENT local b = tostring(smu.source.offmode) "
                "smu.source.offmode = smu.OFFMODE_HIGHZ local c = tostring(smu.source.offmode) "
                "smu.source.func = smu.FUNC_DC_VOLTAGE local d = tostring(smu.source.offmode) "
                "smu.source.func = smu.FUNC_DC_CURRENT "
                "return 'V set:' .. a .. ' I before:' .. b .. ' I set:' .. c .. ' V after:' .. d"
            ),
        )
        print("    interpret: 'I before' NORMAL = off mode is per function; set it for both")

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
        print(
            "    interpret: parked at 7 = latched; 3 = not latched and WAIT needs a new event;"
            " 6 = not latched but WAIT (CLEAR_NEVER) accepted the old event"
        )

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
                    f"local b = buffer.make({size}, buffer.STYLE_STANDARD) "
                    "local c = b.capacity buffer.delete(b) return c"
                ),
            )
            results[f"P5_{size}"] = reply
            if reply.startswith("OK"):
                break

        print("P6 live buffer reads while the trigger model measures (output OFF)")
        cleanup(t, "oe_live")  # Leftovers of an interrupted earlier run.
        try:
            results.update(probe_live_reads(t))
        finally:
            cleanup(t, "oe_live")
        require_idle_off(t)

        print("P7 source + measure function switch inside one trigger model (output OFF)")
        cleanup(t, "oe_switch")  # Leftovers of an interrupted earlier run.
        try:
            results.update(probe_function_switch(t))
        finally:
            cleanup(t, "oe_switch")
        require_idle_off(t)

        print("Final state")
        require_idle_off(t)
        results["final_offmode"] = q(t, "print(smu.source.offmode)")
        results["final_model"] = q(t, "print(trigger.model.state())")
        results["final_event_count"] = q(t, "print(eventlog.getcount(eventlog.SEV_ALL))")
        results["final_defbuffers"] = q(t, "print(defbuffer1.n, defbuffer1.capacity, defbuffer2.n)")
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
