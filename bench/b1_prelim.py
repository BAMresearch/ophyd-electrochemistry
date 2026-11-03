"""Preliminary B1 checks for the sequencer design (docs/sequencer-design.md).

Tier 1 on the 100 ohm four-wire resistor (front), |I| <= 2 mA here (authorized
<= 5 mA), HIGH-Z for both functions, busy line 2 as marker. Each test is its
own trigger model; output OFF is confirmed after each.

T1  counters (output OFF, counted with log events): does an inner
    BRANCH_COUNTER restart when its loop is re-entered, and does
    BLOCK_RESET_BRANCH_COUNT exist and reset it?
T2  eventlog.post timestamp vs host UTC (clock mapping); in-model LOG_EVENT vs
    buffer reading timestamps (t0 on the same clock?)
T3  SOURCE_OUTPUT ON while already on; attempt to reproduce the stale
    smu.source.output reading of E1 (CC -> CV -> CC recall, then OUTPUT OFF)
T4  reading time at 0.5/0.2/0.05 NPLC (auto-zero OFF); loop overhead with
    marker + two limit blocks at 0.1 NPLC; a 6 ms delay-paced loop
T5  live polling of a 30 s, ~200 Hz run in the planned 6-field format

Usage:  uv run --locked --extra visa python bench/b1_prelim.py \\
            --i-confirm-100-ohm-four-wire-resistor [--only T1 ...]
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections.abc import Callable
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
from function_switch_e2 import shutdown
from function_switch_probe import emergency_off
from timing_cutoff_e3 import cleanup, load_model, measure_v, prepare_lists, run_model, source_cc

from ophyd_electrochemistry.keithley.k2460.transport import (
    PyVisaKeithley2460Transport,
    VisaTransportConfig,
)

Result = dict[str, object]
LEVEL_A = 0.002


def source_cv(level: float) -> str:
    return (
        "smu.source.func = smu.FUNC_DC_VOLTAGE smu.source.autorange = smu.OFF "
        "smu.source.range = 2 smu.source.ilimit.level = 0.005 smu.source.readback = smu.ON "
        f"smu.source.level = {level} "
        "smu.measure.func = smu.FUNC_DC_CURRENT smu.measure.sense = smu.SENSE_4WIRE "
        "smu.measure.autorange = smu.OFF smu.measure.range = 0.01 smu.measure.nplc = 1 "
        "smu.measure.autozero.enable = smu.OFF "
    )


def confirm_off(t: PyVisaKeithley2460Transport) -> str:
    """Design section 10: OFF, and if the attribute still reads ON, toggle the function."""
    output = shutdown(t)
    if output == "smu.ON":
        q(
            t,
            guarded(
                "smu.source.func = smu.FUNC_DC_VOLTAGE smu.source.func = smu.FUNC_DC_CURRENT "
                "smu.source.level = 0 return 1"
            ),
        )
        output = "stale ON, after toggle: " + q(t, "print(smu.source.output)")
    return output


def drain_events(t: PyVisaKeithley2460Transport) -> list[str]:
    count = int(float(q(t, "print(eventlog.getcount(eventlog.SEV_ALL))")))
    return [q(t, "print(eventlog.next(eventlog.SEV_ALL))") for _ in range(count)]


def wait_idle(t: PyVisaKeithley2460Transport, limit_s: float = 10.0) -> str:
    started = time.monotonic()
    while True:
        time.sleep(0.1)
        state = q(t, "print(trigger.model.state())")
        if "RUNNING" not in state and "WAITING" not in state:
            return state
        if time.monotonic() - started > limit_s:
            w(t, "trigger.model.abort()")
            return "WATCHDOG " + state


def t1(t: PyVisaKeithley2460Transport) -> tuple[Result, list[str]]:
    """Output OFF: inner counter 3 inside outer counter 2, logged A/B events."""
    result: Result = {}
    lines = []
    variants = {
        "no_reset": [
            'trigger.BLOCK_LOG_EVENT, trigger.LOG_INFO1, "OE_A"',
            "trigger.BLOCK_BRANCH_COUNTER, 3, 1",
            'trigger.BLOCK_LOG_EVENT, trigger.LOG_INFO2, "OE_B"',
            "trigger.BLOCK_BRANCH_COUNTER, 2, 1",
        ],
        "with_reset": [
            "trigger.BLOCK_RESET_BRANCH_COUNT, 3",
            'trigger.BLOCK_LOG_EVENT, trigger.LOG_INFO1, "OE_A"',
            "trigger.BLOCK_BRANCH_COUNTER, 3, 2",
            'trigger.BLOCK_LOG_EVENT, trigger.LOG_INFO2, "OE_B"',
            "trigger.BLOCK_BRANCH_COUNTER, 2, 1",
        ],
    }
    for name, blocks in variants.items():
        drain_events(t)
        setblocks = " ".join(
            f"trigger.model.setblock({i}, {b})" for i, b in enumerate(blocks, start=1)
        )
        reply = q(t, guarded(f'trigger.model.load("Empty") {setblocks} return 1'))
        result[f"{name}_load"] = reply
        if not reply.startswith("OK"):
            lines.append(f"T1 {name}: load failed: {reply}")
            continue
        require_idle_off(t)
        w(t, "trigger.model.initiate()")
        result[f"{name}_state"] = wait_idle(t)
        events = drain_events(t)
        result[f"{name}_events"] = events
        a = sum("OE_A" in e for e in events)
        b = sum("OE_B" in e for e in events)
        errors = [e for e in events if e.split("\t")[0].lstrip("-").isdigit() and "\t1\t" in e]
        lines.append(f"T1 {name}: OE_A x{a}, OE_B x{b}, errors {errors}")
    lines.append(
        "T1 interpret: 6 A + 2 B = inner counter restarts on re-entry; 4 A + 2 B = it does"
        " not (keeps its completed count)"
    )
    return result, lines


def t2(t: PyVisaKeithley2460Transport) -> tuple[Result, list[str]]:
    result: Result = {}
    lines = []
    drain_events(t)
    pings = []
    for _ in range(5):
        before = time.time()
        reply = q(t, guarded('eventlog.post("OE_PING", eventlog.SEV_INFO) return 1'))
        after = time.time()
        pings.append((before, after, reply))
    events = drain_events(t)
    result["ping_replies"] = [p[2] for p in pings]
    result["ping_events"] = events
    stamps = []
    for event in events:
        fields = event.split("\t")
        if "OE_PING" in event and len(fields) >= 6:
            stamps.append(float(fields[-2]) + float(fields[-1]) * 1e-9)
    if len(stamps) == len(pings):
        offsets = [s - (b + a) / 2 for s, (b, a, _) in zip(stamps, pings, strict=True)]
        rtts = [a - b for b, a, _ in pings]
        lines.append(
            f"T2 eventlog.post: instrument - host UTC = {statistics.mean(offsets) * 1e3:.2f} ms"
            f" (spread {(max(offsets) - min(offsets)) * 1e3:.2f} ms), round trip"
            f" {statistics.mean(rtts) * 1e3:.2f} ms"
        )
    else:
        lines.append(f"T2 eventlog.post: {len(stamps)} timestamped pings of {len(pings)}")
    prepare_lists(t, [source_cc(LEVEL_A) + measure_v(1, "OFF")])
    load_model(
        t,
        10,
        [
            'trigger.BLOCK_CONFIG_RECALL, "SE", 1, "ME", 1',
            "trigger.BLOCK_SOURCE_OUTPUT, smu.ON",
            'trigger.BLOCK_LOG_EVENT, trigger.LOG_INFO1, "OE_T0"',
            "trigger.BLOCK_MEASURE_DIGITIZE, oe_e3, 3",
            "trigger.BLOCK_SOURCE_OUTPUT, smu.OFF",
        ],
    )
    drain_events(t)
    run = run_model(t)
    result["model"] = run
    result["absolute"] = q(
        t,
        guarded(
            "local r = '' for i = 1, oe_e3.n do r = r .. oe_e3.seconds[i] .. ' ' "
            ".. string.format('%.9f', oe_e3.fractionalseconds[i]) .. ';' end return r"
        ),
    )
    events = drain_events(t)
    result["model_events"] = events
    t0 = next((e for e in events if "OE_T0" in e), "")
    if t0 and str(result["absolute"]).startswith("OK "):
        fields = t0.split("\t")
        t0_s = float(fields[-2]) + float(fields[-1]) * 1e-9
        first = str(result["absolute"])[3:].split(";")[0].split()
        first_s = float(first[0]) + float(first[1])
        lines.append(f"T2 first reading - OE_T0 log event = {(first_s - t0_s) * 1e3:.3f} ms")
    else:
        lines.append(f"T2 in-model log event / timestamps not parsed: {t0!r}")
    return result, lines


def t3(t: PyVisaKeithley2460Transport) -> tuple[Result, list[str]]:
    prepare_lists(t, [source_cc(LEVEL_A) + measure_v(1, "OFF"), source_cv(0.2)])
    load_model(
        t,
        30,
        [
            'trigger.BLOCK_CONFIG_RECALL, "SE", 1, "ME", 1',
            "trigger.BLOCK_SOURCE_OUTPUT, smu.ON",
            "trigger.BLOCK_MEASURE_DIGITIZE, oe_e3, 5",
            "trigger.BLOCK_SOURCE_OUTPUT, smu.ON",  # already on
            "trigger.BLOCK_MEASURE_DIGITIZE, oe_e3, 5",
            'trigger.BLOCK_CONFIG_RECALL, "SE", 2, "ME", 2',  # CV: output goes off
            "trigger.BLOCK_MEASURE_DIGITIZE, oe_e3, 2",
            'trigger.BLOCK_CONFIG_RECALL, "SE", 1, "ME", 1',  # back to CC, no ON
            "trigger.BLOCK_MEASURE_DIGITIZE, oe_e3, 2",
            "trigger.BLOCK_SOURCE_OUTPUT, smu.OFF",
        ],
    )
    # Not run_model(): the output attribute must be read before the forced OFF.
    require_idle_off(t)
    w(t, "trigger.model.initiate()")
    run: Result = {"model_state": wait_idle(t)}
    run["output_attr_after_model"] = q(t, "print(smu.source.output)")
    run["readout"] = q(
        t,
        guarded(
            "local r = '' for i = 1, oe_e3.n do "
            "r = r .. string.format('%.6f', oe_e3.relativetimestamps[i]) .. ',' "
            ".. string.format('%.8g', oe_e3.readings[i]) .. ',' "
            ".. tostring(oe_e3.sourcestatuses[i]) .. ';' end return r"
        ),
    )
    run["output_final"] = confirm_off(t)
    rows = [r.split(",") for r in str(run["readout"]).removeprefix("OK ").split(";") if r]
    statuses = [int(float(r[2])) for r in rows]
    spacing = [float(b[0]) - float(a[0]) for a, b in zip(rows, rows[1:], strict=False)]
    lines = [
        f"T3 source statuses per reading: {statuses}",
        f"T3 gap across the repeated ON block: {spacing[4] * 1e3:.2f} ms"
        f" (normal {statistics.median(spacing[:4]) * 1e3:.2f} ms)"
        if len(spacing) > 4
        else "T3 too few readings",
        f"T3 smu.source.output after model: {run.get('output_attr_after_model', '?')};"
        f" confirm_off: {run['output_final']}",
    ]
    return {"model": run}, lines


def t3b(t: PyVisaKeithley2460Transport) -> tuple[Result, list[str]]:
    """The design's pattern: OUTPUT ON after every function switch."""
    prepare_lists(t, [source_cc(LEVEL_A) + measure_v(1, "OFF"), source_cv(0.2)])
    load_model(
        t,
        30,
        [
            'trigger.BLOCK_CONFIG_RECALL, "SE", 1, "ME", 1',
            "trigger.BLOCK_SOURCE_OUTPUT, smu.ON",
            "trigger.BLOCK_MEASURE_DIGITIZE, oe_e3, 3",
            'trigger.BLOCK_CONFIG_RECALL, "SE", 2, "ME", 2',
            "trigger.BLOCK_SOURCE_OUTPUT, smu.ON",
            "trigger.BLOCK_MEASURE_DIGITIZE, oe_e3, 3",
            'trigger.BLOCK_CONFIG_RECALL, "SE", 1, "ME", 1',
            "trigger.BLOCK_SOURCE_OUTPUT, smu.ON",
            "trigger.BLOCK_MEASURE_DIGITIZE, oe_e3, 3",
            "trigger.BLOCK_SOURCE_OUTPUT, smu.OFF",
        ],
    )
    require_idle_off(t)
    w(t, "trigger.model.initiate()")
    run: Result = {"model_state": wait_idle(t)}
    run["output_attr_after_model"] = q(t, "print(smu.source.output)")
    run["readout"] = q(
        t,
        guarded(
            "local r = '' for i = 1, oe_e3.n do "
            "r = r .. string.format('%.8g', oe_e3.readings[i]) .. ',' "
            ".. tostring(oe_e3.sourcestatuses[i]) .. ';' end return r"
        ),
    )
    run["output_final"] = confirm_off(t)
    return {"model": run}, [
        f"T3b readings,status: {run['readout']}",
        f"T3b smu.source.output after model: {run['output_attr_after_model']};"
        f" confirm_off: {run['output_final']}",
    ]


def loop_blocks(delay_s: float, marker: bool, limits: int) -> list[str]:
    """One sampling loop as the sequencer will emit it (blocks 3.. of the model)."""
    body = [f"trigger.BLOCK_DELAY_CONSTANT, {delay_s}", "trigger.BLOCK_MEASURE_DIGITIZE, oe_e3, 1"]
    if marker:
        body.append("trigger.BLOCK_DIGITAL_IO, 0, 2")
    body += ["trigger.BLOCK_BRANCH_LIMIT_CONSTANT, trigger.LIMIT_OUTSIDE, -1, 1, 99, 0"] * limits
    return body


def t4(t: PyVisaKeithley2460Transport) -> tuple[Result, list[str]]:
    result: Result = {}
    lines = []
    profiles = [0.5, 0.2, 0.05]
    prepare_lists(t, [source_cc(LEVEL_A) + measure_v(n, "OFF") for n in profiles])
    blocks = [
        'trigger.BLOCK_CONFIG_RECALL, "SE", 1, "ME", 1',
        "trigger.BLOCK_SOURCE_OUTPUT, smu.ON",
    ]
    for index in range(1, len(profiles) + 1):
        if index > 1:
            blocks.append(f'trigger.BLOCK_CONFIG_RECALL, "SE", {index}, "ME", {index}')
        blocks.append("trigger.BLOCK_MEASURE_DIGITIZE, oe_e3, 30")
    blocks.append("trigger.BLOCK_SOURCE_OUTPUT, smu.OFF")
    load_model(t, 100, blocks)
    run = run_model(t)
    result["continuous"] = run
    rows = [r.split(",") for r in str(run["readout"]).removeprefix("OK ").split(";") if r]
    for index, nplc in enumerate(profiles):
        chunk = rows[index * 30 : (index + 1) * 30]
        if len(chunk) < 3:
            continue
        spacing = [float(b[0]) - float(a[0]) for a, b in zip(chunk, chunk[1:], strict=False)]
        values = [float(r[1]) for r in chunk]
        lines.append(
            f"T4 {nplc} NPLC: spacing {statistics.median(spacing) * 1e3:.3f} ms,"
            f" std {statistics.stdev(values) * 1e6:.1f} uV"
        )
    cleanup(t)
    # Sequencer-shaped loops at 0.1 NPLC: overhead (delay 0) and a 6 ms target.
    for name, delay_s, nplc in (("overhead", 0.0, 0.1), ("6ms", None, 0.1), ("5ms", None, 0.05)):
        prepare_lists(t, [source_cc(LEVEL_A) + measure_v(nplc, "OFF")])
        if delay_s is None:
            base = result.get(f"loop_{nplc}_period_s")
            if not isinstance(base, float):
                lines.append(f"T4 {name}: skipped (no overhead measurement at {nplc} NPLC)")
                continue
            target = 0.006 if name == "6ms" else 0.005
            delay_s = target - base
            if delay_s < 0:
                lines.append(f"T4 {name}: infeasible, loop alone takes {base * 1e3:.3f} ms")
                continue
        head = [
            'trigger.BLOCK_CONFIG_RECALL, "SE", 1, "ME", 1',
            "trigger.BLOCK_SOURCE_OUTPUT, smu.ON",
            "trigger.BLOCK_DIGITAL_IO, 2, 2",
        ]
        body = loop_blocks(delay_s, marker=True, limits=2)
        first = len(head) + 1
        body = [b.replace(", 99, 0", f", 99, {first + 1}") for b in body]
        stop = len(head) + len(body) + 2
        body = [b.replace(", 99,", f", {stop},") for b in body]
        blocks = head + body + [f"trigger.BLOCK_BRANCH_COUNTER, 50, {first}"]
        blocks += ["trigger.BLOCK_SOURCE_OUTPUT, smu.OFF", "trigger.BLOCK_DIGITAL_IO, 0, 2"]
        load_model(t, 60, blocks)
        run = run_model(t)
        result[name] = run
        rows = [r.split(",") for r in str(run["readout"]).removeprefix("OK ").split(";") if r]
        spacing = [float(b[0]) - float(a[0]) for a, b in zip(rows, rows[1:], strict=False)]
        if len(spacing) < 5:
            lines.append(f"T4 {name}: only {len(rows)} readings")
            continue
        period = statistics.median(spacing)
        if name == "overhead":
            result[f"loop_{nplc}_period_s"] = period
            result["loop_0.05_period_s"] = None
        lines.append(
            f"T4 loop {name} at {nplc} NPLC, delay {delay_s * 1e3:.3f} ms: period"
            f" {period * 1e3:.4f} ms (min {min(spacing) * 1e3:.4f}, max {max(spacing) * 1e3:.4f}),"
            f" {len(rows)} readings"
        )
        cleanup(t)
        if name == "overhead":
            # Overhead at 0.05 NPLC, needed for the 5 ms loop.
            prepare_lists(t, [source_cc(LEVEL_A) + measure_v(0.05, "OFF")])
            load_model(t, 60, blocks)
            run = run_model(t)
            rows = [r.split(",") for r in str(run["readout"]).removeprefix("OK ").split(";") if r]
            sp = [float(b[0]) - float(a[0]) for a, b in zip(rows, rows[1:], strict=False)]
            if len(sp) > 5:
                result["loop_0.05_period_s"] = statistics.median(sp)
                period_ms = statistics.median(sp) * 1e3
                lines.append(f"T4 loop overhead at 0.05 NPLC: period {period_ms:.4f} ms")
            cleanup(t)
    return result, lines


def t5(t: PyVisaKeithley2460Transport) -> tuple[Result, list[str]]:
    count = 6000
    prepare_lists(t, [source_cc(LEVEL_A) + measure_v(0.1, "OFF")])
    load_model(
        t,
        count + 10,
        [
            'trigger.BLOCK_CONFIG_RECALL, "SE", 1, "ME", 1',
            "trigger.BLOCK_SOURCE_OUTPUT, smu.ON",
            f"trigger.BLOCK_MEASURE_DIGITIZE, oe_e3, {count}",
            "trigger.BLOCK_SOURCE_OUTPUT, smu.OFF",
        ],
    )
    original_precision = q(t, "print(format.asciiprecision)")
    q(t, guarded("format.asciiprecision = 15 return format.asciiprecision"))
    require_idle_off(t)
    w(t, "trigger.model.initiate()")
    started = time.monotonic()
    offset, polls, sizes, durations, gaps = 0, 0, [], [], 0
    last_time = None
    state = ""
    while time.monotonic() - started < 60:
        time.sleep(1.0)
        state = q(t, "print(trigger.model.state())")
        n = int(float(q(t, "print(oe_e3.n)")))
        while offset < n:
            last = min(n, offset + 250)  # ~28 KB per reply at 15 digits
            begin = time.monotonic()
            reply = q(
                t,
                f"printbuffer({offset + 1}, {last}, oe_e3.seconds, oe_e3.fractionalseconds,"
                " oe_e3.readings, oe_e3.sourcevalues, oe_e3.statuses, oe_e3.sourcestatuses)",
            )
            durations.append(time.monotonic() - begin)
            sizes.append(len(reply))
            values = [v.strip() for v in reply.split(",")]
            if len(values) != 6 * (last - offset):
                gaps += 1
            for i in range(0, len(values) - 5, 6):
                stamp = float(values[i]) + float(values[i + 1])
                if last_time is not None and not 0 < stamp - last_time < 0.05:
                    gaps += 1
                last_time = stamp
            offset = last
            polls += 1
        if "RUNNING" not in state and "WAITING" not in state and offset >= n:
            break
    output = shutdown(t)
    q(t, guarded(f"format.asciiprecision = {int(float(original_precision))} return 1"))
    lines = [
        f"T5 {offset} of {count} records in {polls} reads; reply size max {max(sizes)} B;"
        f" read time median {statistics.median(durations) * 1e3:.1f} ms,"
        f" max {max(durations) * 1e3:.1f} ms; discontinuities {gaps}; final state {state}",
    ]
    return {"records": offset, "polls": polls, "output_final": output}, lines


TESTS: dict[str, Callable[[PyVisaKeithley2460Transport], tuple[Result, list[str]]]] = {
    "T1": t1,
    "T2": t2,
    "T3": t3,
    "T3b": t3b,
    "T4": t4,
    "T5": t5,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resource", default=RESOURCE)
    parser.add_argument(
        "--i-confirm-100-ohm-four-wire-resistor", action="store_true", required=True
    )
    parser.add_argument("--only", nargs="*", choices=sorted(TESTS))
    parser.add_argument("--tag", default="b1-prelim")
    args = parser.parse_args()
    results: dict[str, object] = {}
    summary: list[str] = []

    drain_stale_output(args.resource)
    config = VisaTransportConfig(
        resource_name=args.resource, backend="@py", required_language="TSP", io_timeout_ms=10_000
    )
    t = PyVisaKeithley2460Transport(config)
    t.connect()
    try:
        if t.identity.serial_number != EXPECTED_SERIAL:
            raise SystemExit(f"STOP: unexpected serial {t.identity.serial_number}")
        require_idle_off(t)
        if q(t, "print(smu.terminals)") != "smu.TERMINALS_FRONT":
            raise SystemExit("STOP: resistor is expected on the front terminals")
        cleanup(t)
        results["offmode"] = q(
            t,
            guarded(
                "smu.source.func = smu.FUNC_DC_VOLTAGE smu.source.offmode = smu.OFFMODE_HIGHZ "
                "local v = smu.source.offmode "
                "smu.source.func = smu.FUNC_DC_CURRENT smu.source.offmode = smu.OFFMODE_HIGHZ "
                "local i = smu.source.offmode "
                "digio.line[2].mode = digio.MODE_DIGITAL_OUT digio.line[2].state = 0 "
                "return tostring(i) .. ' / ' .. tostring(v)"
            ),
        )
        if results["offmode"] != "OK smu.OFFMODE_HIGHZ / smu.OFFMODE_HIGHZ":
            raise SystemExit("STOP: HIGH-Z not confirmed for both functions")
        results["events_before"] = drain_events(t)
        for name in args.only or sorted(TESTS):
            print(name)
            try:
                results[name], lines = TESTS[name](t)
            except SystemExit:
                raise
            except Exception as exc:  # one test's parse problem must not skip shutdown
                results[name], lines = {"exception": repr(exc)}, [f"{name} exception: {exc!r}"]
            summary += lines
            print("\n".join(lines), flush=True)
            output = confirm_off(t)
            results[f"{name}_output_after"] = output
            if not output.endswith("smu.OFF"):
                raise SystemExit(f"STOP: output not confirmed OFF after {name}: {output}")
            cleanup(t)
            results[f"{name}_events_after"] = drain_events(t)
    finally:
        try:
            results["output_final"] = confirm_off(t)
            cleanup(t)
            results["events_final"] = drain_events(t)
        except Exception as exc:
            results["shutdown_error"] = repr(exc)
            results["output_final"] = emergency_off(args.resource)
            raise
        finally:
            t.close()
            save(args.resource, summary, results, args.tag)
    print(f"OUTPUT FINAL: {results['output_final']}")


def save(resource: str, summary: list[str], results: dict[str, object], tag: str) -> None:
    stamp = datetime.now(UTC)
    out = Path("docs/evidence") / f"k2460-{stamp:%Y-%m-%d}-{tag}.json"
    out.write_text(
        json.dumps(
            {
                "schema": "ophyd-electrochemistry.bench-probe.v1",
                "observed_at_utc": stamp.isoformat(),
                "resource": resource,
                "load": "100 ohm 1 W resistor, four-wire, front terminals (user-confirmed)",
                "authorization": "user 2026-11-03: |I| <= 5 mA, busy line 2; this run 2 mA",
                "summary": summary,
                "results": results,
                "transcript_entries": len(log),
            },
            indent=2,
        )
        + "\n"
    )
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
