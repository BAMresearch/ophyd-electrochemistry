"""Energized probes E3 (measurement timing) and E4 (limit-branch cutoff).

Tier 1 on the 100 ohm four-wire resistor (front), |I| <= 5 mA, vlimit 1 V,
HIGH-Z for both functions, busy line 2 as marker. Current source only, so
config recalls keep the output on (E2).

E3  CC 4 mA (~0.41 V); measure entries 1/0.1/0.01 NPLC x auto-zero ON/OFF,
    20 readings each (2 V range, 4-wire). Reports reading spacing and noise.
E4  CC 2 mA (~0.205 V), 10 samples, each followed by a LIMIT_OUTSIDE branch
    with window [-1, 0.3] V: must not fire. Then CC 4 mA (~0.41 V) with the same
    branch: must fire on the first sample and jump to a 0 A rest of 5 samples.
    Expected 16 readings; an early branch in step 1 jumps straight to output
    OFF (fewer readings, no rest readings).

Usage:  uv run --locked --extra visa python bench/timing_cutoff_e3.py \\
            --i-confirm-100-ohm-four-wire-resistor
"""

from __future__ import annotations

import argparse
import json
import statistics
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
from function_switch_e2 import shutdown
from function_switch_probe import emergency_off

from ophyd_electrochemistry.keithley.k2460.transport import (
    PyVisaKeithley2460Transport,
    VisaTransportConfig,
)

WATCHDOG_S = 20.0
PROFILES = [(1, "ON"), (1, "OFF"), (0.1, "ON"), (0.1, "OFF"), (0.01, "ON"), (0.01, "OFF")]
E3_COUNT = 20


def source_cc(level: float) -> str:
    return (
        "smu.source.func = smu.FUNC_DC_CURRENT smu.source.autorange = smu.OFF "
        "smu.source.range = 0.01 smu.source.vlimit.level = 1 smu.source.readback = smu.ON "
        f"smu.source.level = {level} "
    )


def measure_v(nplc: float, autozero: str) -> str:
    return (
        "smu.measure.func = smu.FUNC_DC_VOLTAGE smu.measure.sense = smu.SENSE_4WIRE "
        "smu.measure.autorange = smu.OFF smu.measure.range = 2 "
        f"smu.measure.nplc = {nplc} smu.measure.autozero.enable = smu.{autozero} "
    )


STORE = 'smu.source.configlist.store("SE") smu.measure.configlist.store("ME") '


def cleanup(t: PyVisaKeithley2460Transport) -> None:
    q(
        t,
        guarded(
            "if oe_e3 ~= nil then buffer.delete(oe_e3) oe_e3 = nil end "
            "if oe_e3_lists then smu.source.configlist.delete('SE') "
            "smu.measure.configlist.delete('ME') oe_e3_lists = nil end "
            + source_cc(0)
            + measure_v(1, "ON")
            + 'trigger.model.load("Empty") return 1'
        ),
    )


def prepare_lists(t: PyVisaKeithley2460Transport, entries: list[str]) -> None:
    q(
        t,
        guarded(
            'smu.source.configlist.create("SE") smu.measure.configlist.create("ME") '
            "oe_e3_lists = true return 1"
        ),
    )
    for index, entry in enumerate(entries, start=1):
        reply = q(t, guarded(entry + STORE + "return 1"))
        if not reply.startswith("OK"):
            raise SystemExit(f"STOP: config entry {index} failed before arming: {reply}")
    q(t, guarded(source_cc(0) + measure_v(1, "ON") + "return 1"))  # safe active settings


def load_model(t: PyVisaKeithley2460Transport, size: int, blocks: list[str]) -> None:
    """Load blocks five per command line (long lines are rejected by the instrument)."""
    reply = q(
        t,
        guarded(
            "if oe_e3 ~= nil then buffer.delete(oe_e3) end "
            f"oe_e3 = buffer.make({size}, buffer.STYLE_STANDARD) "
            'trigger.model.load("Empty") return 1'
        ),
    )
    lines = [f"trigger.model.setblock({i}, {args})" for i, args in enumerate(blocks, start=1)]
    for start in range(0, len(lines), 5):
        if not reply.startswith("OK"):
            raise SystemExit(f"STOP: model setup failed before arming: {reply}")
        reply = q(t, guarded(" ".join(lines[start : start + 5]) + " return 1"))
    if not reply.startswith("OK"):
        raise SystemExit(f"STOP: model setup failed before arming: {reply}")


def run_model(t: PyVisaKeithley2460Transport) -> dict[str, str]:
    require_idle_off(t)
    w(t, "trigger.model.initiate()")
    started = time.monotonic()
    result: dict[str, str] = {}
    while True:
        time.sleep(0.2)
        state = q(t, "print(trigger.model.state())")
        if "RUNNING" not in state and "WAITING" not in state:
            break
        if time.monotonic() - started > WATCHDOG_S:
            result["watchdog"] = state
            break
    result["model_state"] = state
    result["readout"] = q(
        t,
        guarded(
            "local r = '' for i = 1, oe_e3.n do "
            "r = r .. string.format('%.6f', oe_e3.relativetimestamps[i]) .. ',' "
            ".. string.format('%.8g', oe_e3.readings[i]) .. ',' "
            ".. tostring(oe_e3.sourcestatuses[i]) .. ';' end return r"
        ),
    )
    result["output_final"] = shutdown(t)
    if result["output_final"] != "smu.OFF":
        raise SystemExit("STOP: output not confirmed OFF")
    return result


def rows(readout: str) -> list[tuple[float, float, int]]:
    return [
        (float(a), float(b), int(float(c)))
        for a, b, c in (r.split(",") for r in readout.removeprefix("OK ").split(";") if r)
    ]


def e3(t: PyVisaKeithley2460Transport) -> tuple[dict[str, str], list[str]]:
    prepare_lists(t, [source_cc(0.004) + measure_v(n, az) for n, az in PROFILES])
    blocks = ["trigger.BLOCK_DIGITAL_IO, 2, 2"]
    for index in range(1, len(PROFILES) + 1):
        blocks.append(f'trigger.BLOCK_CONFIG_RECALL, "SE", {index}, "ME", {index}')
        if index == 1:
            blocks.append("trigger.BLOCK_SOURCE_OUTPUT, smu.ON")
        blocks.append(f"trigger.BLOCK_MEASURE_DIGITIZE, oe_e3, {E3_COUNT}")
    blocks += ["trigger.BLOCK_SOURCE_OUTPUT, smu.OFF", "trigger.BLOCK_DIGITAL_IO, 0, 2"]
    load_model(t, E3_COUNT * len(PROFILES) + 5, blocks)
    result = run_model(t)
    data = rows(result["readout"])
    summary = []
    for index, (nplc, az) in enumerate(PROFILES):
        chunk = data[index * E3_COUNT : (index + 1) * E3_COUNT]
        if len(chunk) < 3:
            continue
        spacing = [b[0] - a[0] for a, b in zip(chunk, chunk[1:], strict=False)]
        values = [row[1] for row in chunk]
        summary.append(
            f"E3 {nplc} NPLC autozero {az}: spacing median {statistics.median(spacing) * 1e3:.3f}"
            f" ms (min {min(spacing) * 1e3:.3f}, max {max(spacing) * 1e3:.3f}), mean"
            f" {statistics.mean(values):.6f} V, std {statistics.stdev(values) * 1e6:.1f} uV,"
            f" statuses {sorted({row[2] for row in chunk})}"
        )
    return result, summary


def e4(t: PyVisaKeithley2460Transport) -> tuple[dict[str, str], list[str]]:
    prepare_lists(
        t,
        [
            source_cc(0.002) + measure_v(1, "OFF"),
            source_cc(0.004) + measure_v(1, "OFF"),
            source_cc(0) + measure_v(1, "OFF"),
        ],
    )
    window = "trigger.LIMIT_OUTSIDE, -1, 0.3"
    # No unreachable blocks: a first version with a spacer block after a
    # BRANCH_ALWAYS failed validation at initiate (event 2709).
    blocks = [
        "trigger.BLOCK_DIGITAL_IO, 2, 2",  # 1
        'trigger.BLOCK_CONFIG_RECALL, "SE", 1, "ME", 1',  # 2 step 1: 2 mA
        "trigger.BLOCK_SOURCE_OUTPUT, smu.ON",  # 3
        "trigger.BLOCK_MEASURE_DIGITIZE, oe_e3, 1",  # 4
        f"trigger.BLOCK_BRANCH_LIMIT_CONSTANT, {window}, 13, 4",  # 5 must not fire
        "trigger.BLOCK_BRANCH_COUNTER, 9, 4",  # 6 ten samples
        'trigger.BLOCK_CONFIG_RECALL, "SE", 2, "ME", 2',  # 7 step 2: 4 mA
        "trigger.BLOCK_MEASURE_DIGITIZE, oe_e3, 1",  # 8
        f"trigger.BLOCK_BRANCH_LIMIT_CONSTANT, {window}, 11, 8",  # 9 must fire
        "trigger.BLOCK_BRANCH_COUNTER, 9, 8",  # 10
        'trigger.BLOCK_CONFIG_RECALL, "SE", 3, "ME", 3',  # 11 rest 0 A
        "trigger.BLOCK_MEASURE_DIGITIZE, oe_e3, 5",  # 12
        "trigger.BLOCK_SOURCE_OUTPUT, smu.OFF",  # 13 (an early cutoff lands here)
        "trigger.BLOCK_DIGITAL_IO, 0, 2",  # 14
    ]
    load_model(t, 40, blocks)
    result = run_model(t)
    data = rows(result["readout"])
    volts = [f"{row[1]:.4f}" for row in data]
    verdict = (
        "PASS: 10 samples at ~0.2 V without cutoff, cutoff on the first ~0.41 V sample,"
        " then 5 rest samples"
        if len(data) == 16
        and all(row[1] < 0.3 for row in data[:10])
        and data[10][1] > 0.3
        and all(abs(row[1]) < 0.05 for row in data[11:])
        else "UNEXPECTED path"
    )
    return result, [f"E4 {len(data)} readings: {' '.join(volts)}", f"E4 {verdict}"]


PROBES = [("E3", e3), ("E4", e4)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resource", default=RESOURCE)
    parser.add_argument(
        "--i-confirm-100-ohm-four-wire-resistor", action="store_true", required=True
    )
    parser.add_argument("--only", choices=["E3", "E4"], help="run one probe")
    args = parser.parse_args()
    results: dict[str, object] = {}
    summary: list[str] = []
    probes = [(name, probe) for name, probe in PROBES if args.only in (None, name)]

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
        for name, probe in probes:
            print(name)
            results[name], lines = probe(t)
            summary += lines
            cleanup(t)
    finally:
        try:
            results["output_final"] = shutdown(t)
            cleanup(t)
            count = int(float(q(t, "print(eventlog.getcount(eventlog.SEV_ALL))")))
            results["event_log"] = [
                q(t, "print(eventlog.next(eventlog.SEV_ALL))") for _ in range(count)
            ]
        except Exception as exc:
            results["shutdown_error"] = repr(exc)
            results["output_final"] = emergency_off(args.resource)
            raise
        finally:
            t.close()

    print("\n".join(summary))
    print(f"OUTPUT FINAL: {results['output_final']}")
    stamp = datetime.now(UTC)
    suffix = "e3-e4" if args.only is None else args.only.lower()
    out = Path("docs/evidence") / f"k2460-{stamp:%Y-%m-%d}-timing-cutoff-{suffix}.json"
    out.write_text(
        json.dumps(
            {
                "schema": "ophyd-electrochemistry.bench-probe.v1",
                "observed_at_utc": stamp.isoformat(),
                "resource": args.resource,
                "load": "100 ohm 1 W resistor, four-wire, front terminals (user-confirmed)",
                "authorization": "user 2026-11-03: |I| <= 5 mA, busy line 2 as marker",
                "summary": summary,
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
