"""Scope checks S1/S2 with a sequencer-shaped trigger model (design section 5).

Tier 1 on the 100 ohm four-wire resistor (front), |I| <= 4.9 mA (~0.50 V), HIGH-Z for both
functions. Lines as in Keithley2460Config: 1 = step marker (READY line),
2 = busy, 3 = START input (external mode only). Scope: CH1 line 1, CH2 line 2,
CH3 generator, optional CH4 across the resistor.

Run: rest 0 A 0.1 s, then (+4.9 mA, 0 A, -4.9 mA, 0 A; 0.1 s each) x 3 repeats,
16 samples per step at 6.25 ms, 0.1 NPLC, auto-zero OFF. 13 steps, 208
readings, about 1.3 s with the output on. Each step: counter reset, recall,
marker high, then DELAY / MEASURE / marker low / limit [-0.8, 0.8] V -> stop /
counter. Prologue: optional WAIT on line 3 (rising), log event OE_T0, busy high.

  S1  --start immediate   (scope trigger: CH2 rising)
  S2  --start external    (scope trigger: CH3 rising; press the generator's
                           trigger within --wait-s seconds after "ARMED")

Usage:  uv run --locked --extra visa python bench/marker_start_s1.py \\
            --i-confirm-100-ohm-four-wire-resistor --start immediate
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

from b1_prelim import confirm_off, drain_events
from feature_probe import (
    EXPECTED_SERIAL,
    RESOURCE,
    drain_stale_output,
    guarded,
    q,
    require_idle_off,
    w,
)
from function_switch_probe import emergency_off
from timing_cutoff_e3 import cleanup, load_model, measure_v, prepare_lists, source_cc

from ophyd_electrochemistry.keithley.k2460.transport import (
    PyVisaKeithley2460Transport,
    VisaTransportConfig,
)

SAMPLES = 16
INTERVAL_S = 0.00625
LOOP_PERIOD_S = 0.004968  # T4: 0.1 NPLC loop with marker + limit + counter at zero delay
LEVELS = {"rest": 0.0, "plus": 0.0049, "minus": -0.0049}  # entries 1-3; ~0.50 V on 102.5 ohm
REPEATS = 3
WINDOW = "trigger.LIMIT_OUTSIDE, -0.8, 0.8"  # stop window; vlimit 1 V


def build_blocks(external: bool) -> list[str]:
    blocks: list[str] = []

    def add(block: str) -> int:
        blocks.append(block)
        return len(blocks)

    delay = INTERVAL_S - LOOP_PERIOD_S
    if external:
        add("trigger.BLOCK_WAIT, trigger.EVENT_DIGIO3, trigger.CLEAR_ENTER")
    add('trigger.BLOCK_LOG_EVENT, trigger.LOG_INFO1, "OE_T0"')
    add("trigger.BLOCK_DIGITAL_IO, 2, 2")  # busy high

    def step(entry: int, first: bool) -> int:
        start = len(blocks) + 1
        counter = start + (8 if first else 7)
        add(f"trigger.BLOCK_RESET_BRANCH_COUNT, {counter}")
        add(f'trigger.BLOCK_CONFIG_RECALL, "SE", {entry}, "ME", {entry}')
        if first:
            add("trigger.BLOCK_SOURCE_OUTPUT, smu.ON")
        add("trigger.BLOCK_DIGITAL_IO, 1, 1")  # marker high
        loop = add(f"trigger.BLOCK_DELAY_CONSTANT, {delay:.6f}")
        measure = add("trigger.BLOCK_MEASURE_DIGITIZE, oe_e3, 1")
        add("trigger.BLOCK_DIGITAL_IO, 0, 1")  # marker low
        add(f"trigger.BLOCK_BRANCH_LIMIT_CONSTANT, {WINDOW}, @STOP@, {measure}")
        assert add(f"trigger.BLOCK_BRANCH_COUNTER, {SAMPLES}, {loop}") == counter
        return start

    step(1, first=True)  # initial rest
    outer_reset = add("trigger.BLOCK_RESET_BRANCH_COUNT, OUTER")
    block_start = step(2, first=False)
    for entry in (1, 3, 1):
        step(entry, first=False)
    outer = add(f"trigger.BLOCK_BRANCH_COUNTER, {REPEATS}, {block_start}")
    add("trigger.BLOCK_SOURCE_OUTPUT, smu.OFF")
    add("trigger.BLOCK_DIGITAL_IO, 0, 3")
    add('trigger.BLOCK_LOG_EVENT, trigger.LOG_INFO2, "OE_END"')
    branch_done = add("trigger.BLOCK_BRANCH_ALWAYS, DONE")
    stop = add("trigger.BLOCK_SOURCE_OUTPUT, smu.OFF")
    add("trigger.BLOCK_DIGITAL_IO, 0, 3")
    add('trigger.BLOCK_LOG_EVENT, trigger.LOG_INFO4, "OE_STOP"')
    done = add('trigger.BLOCK_LOG_EVENT, trigger.LOG_INFO3, "OE_DONE"')
    blocks[outer_reset - 1] = blocks[outer_reset - 1].replace("OUTER", str(outer))
    blocks[branch_done - 1] = blocks[branch_done - 1].replace("DONE", str(done))
    return [b.replace("@STOP@", str(stop)) for b in blocks]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resource", default=RESOURCE)
    parser.add_argument(
        "--i-confirm-100-ohm-four-wire-resistor", action="store_true", required=True
    )
    parser.add_argument("--start", choices=["immediate", "external"], required=True)
    parser.add_argument("--wait-s", type=float, default=90.0)
    args = parser.parse_args()
    external = args.start == "external"
    results: dict[str, object] = {"start": args.start}
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
        results["setup"] = q(
            t,
            guarded(
                "smu.source.func = smu.FUNC_DC_VOLTAGE smu.source.offmode = smu.OFFMODE_HIGHZ "
                "local v = smu.source.offmode "
                "smu.source.func = smu.FUNC_DC_CURRENT smu.source.offmode = smu.OFFMODE_HIGHZ "
                "local i = smu.source.offmode "
                "digio.line[1].mode = digio.MODE_DIGITAL_OUT digio.line[1].state = 0 "
                "digio.line[2].mode = digio.MODE_DIGITAL_OUT digio.line[2].state = 0 "
                "digio.line[3].mode = digio.MODE_TRIGGER_IN "
                "trigger.digin[3].edge = trigger.EDGE_RISING trigger.digin[3].clear() "
                "return tostring(i) .. ' / ' .. tostring(v)"
            ),
        )
        if results["setup"] != "OK smu.OFFMODE_HIGHZ / smu.OFFMODE_HIGHZ":
            raise SystemExit(f"STOP: setup/HIGH-Z not confirmed: {results['setup']}")
        prepare_lists(t, [source_cc(level) + measure_v(0.1, "OFF") for level in LEVELS.values()])
        blocks = build_blocks(external)
        results["blocks"] = blocks
        load_model(t, SAMPLES * 13 + 16, blocks)
        drain_events(t)
        require_idle_off(t)
        w(t, "trigger.model.initiate()")
        armed = time.time()
        print(f"ARMED ({args.start}); {len(blocks)} blocks", flush=True)
        while True:
            time.sleep(0.2)
            state = q(t, "print(trigger.model.state())")
            if "RUNNING" not in state and "WAITING" not in state:
                break
            if time.time() - armed > (args.wait_s if external else 10):
                results["watchdog"] = state
                w(t, "trigger.model.abort()")
                break
        results["model_state"] = state
        results["output_attr_after_model"] = q(t, "print(smu.source.output)")
        results["readout"] = q(
            t,
            guarded(
                "local r = '' for i = 1, oe_e3.n do "
                "r = r .. oe_e3.seconds[i] .. ',' "
                ".. string.format('%.9f', oe_e3.fractionalseconds[i]) .. ',' "
                ".. string.format('%.6g', oe_e3.readings[i]) .. ',' "
                ".. tostring(oe_e3.sourcestatuses[i]) .. ';' end return r"
            ),
        )
        results["events"] = drain_events(t)
        results["output_final"] = confirm_off(t)
        summary += analyse(results)
    finally:
        try:
            results["output_final_check"] = confirm_off(t)
            cleanup(t)
        except Exception as exc:
            results["shutdown_error"] = repr(exc)
            results["output_final_check"] = emergency_off(args.resource)
            raise
        finally:
            t.close()
            stamp = datetime.now(UTC)
            out = Path("docs/evidence") / f"k2460-{stamp:%Y-%m-%d}-scope-s1-{args.start}.json"
            out.write_text(
                json.dumps(
                    {
                        "schema": "ophyd-electrochemistry.bench-probe.v1",
                        "observed_at_utc": stamp.isoformat(),
                        "load": "100 ohm 1 W resistor, four-wire, front (user-confirmed)",
                        "authorization": "user 3 Nov: |I| <= 5 mA; run at 4.9 mA",
                        "summary": summary,
                        "results": results,
                    },
                    indent=2,
                )
                + "\n"
            )
            print(f"Saved {out}")
    print("\n".join(summary))
    print(f"OUTPUT FINAL: {results.get('output_final_check')}")


def analyse(results: dict[str, object]) -> list[str]:
    rows = [r.split(",") for r in str(results["readout"]).removeprefix("OK ").split(";") if r]
    times = [float(r[0]) + float(r[1]) for r in rows]
    volts = [float(r[2]) for r in rows]
    statuses = sorted({int(float(r[3])) for r in rows})
    events = [str(e) for e in results["events"]]  # type: ignore[union-attr]
    # Log-event entries read "TM path #1 block #N logged: OE_T0".
    stamps = {
        name: float(e.split("\t")[-2]) + float(e.split("\t")[-1]) * 1e-9
        for e in events
        for name in ("OE_T0", "OE_END", "OE_STOP", "OE_DONE")
        if f"logged: {name}\t" in e
    }
    lines = [
        f"S1 model {results['model_state']}; {len(rows)} readings (expected {SAMPLES * 13});"
        f" source statuses {statuses}; output attribute after model"
        f" {results['output_attr_after_model']}",
        f"S1 log events: {sorted(stamps)}",
    ]
    if "OE_T0" in stamps and times:
        t0 = stamps["OE_T0"]
        lines.append(f"S1 first reading {1e3 * (times[0] - t0):.3f} ms after OE_T0")
        steps = [times[k * SAMPLES : (k + 1) * SAMPLES] for k in range(len(times) // SAMPLES)]
        durations = [1e3 * (s[-1] - (steps[i - 1][-1] if i else t0)) for i, s in enumerate(steps)]
        lines.append(f"S1 output on until first reading: {1e3 * (times[0] - t0):.3f} ms")
        means = [statistics.mean(volts[k * SAMPLES : (k + 1) * SAMPLES]) for k in range(len(steps))]
        lines.append("S1 step durations ms: " + " ".join(f"{d:.3f}" for d in durations))
        lines.append("S1 step mean V: " + " ".join(f"{m:+.4f}" for m in means))
        spacing = [b - a for a, b in zip(times, times[1:], strict=False)]
        lines.append(
            f"S1 sample spacing median {1e3 * statistics.median(spacing):.4f} ms,"
            f" max {1e3 * max(spacing):.4f} ms; total {1e3 * (times[-1] - t0):.2f} ms"
            f" (nominal {1e3 * 13 * SAMPLES * INTERVAL_S:.2f})"
        )
    return lines


if __name__ == "__main__":
    main()
