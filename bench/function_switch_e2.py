"""Energized config-list recall probe E2 (tier 1: 100 ohm resistor, four-wire only).

E1 showed that a config-list recall which changes the source function inside a
running trigger model turns the output OFF (source status lost STAT_OUTPUT=128).
E2 repeats with a corrected setup order (source settings before the measure
function, so ranges and limits are not clamped) and three variants, 10 readings
per phase at 1 NPLC, output on for about 1.2 s per variant:

  A  CC +4 mA -> CV 0.30 V                       (function switch, no re-enable)
  B  CC +4 mA -> CV 0.30 V with OUTPUT ON block  (switch, then re-enable)
  C  CC +4 mA -> CC 0 A -> CC -2 mA              (same function: the pulse case)

Expected on 100 ohm: 0.40 V at 4 mA; 3.0 mA at 0.30 V; -0.20 V at -2 mA.
Limits authorized by the user on 2026-11-03: |I| <= 5 mA (ilimit 5 mA, vlimit
1 V). Busy line 2 marks each variant. HIGH-Z for both functions. Output state is
judged from buffer source statuses (STAT_OUTPUT=128, STAT_LIMIT=32), because
smu.source.output was seen to report ON after an in-model function switch.

Usage:  uv run --locked --extra visa python bench/function_switch_e2.py \\
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
from function_switch_probe import emergency_off

from ophyd_electrochemistry.keithley.k2460.transport import (
    PyVisaKeithley2460Transport,
    VisaTransportConfig,
)

COUNT = 10
WATCHDOG_S = 10.0
STAT_OUTPUT, STAT_LIMIT = 128, 32


def cc(level: float) -> str:
    """Current source (10 mA range, 1 V limit), then measure V on 2 V."""
    return (
        "smu.source.func = smu.FUNC_DC_CURRENT smu.source.autorange = smu.OFF "
        "smu.source.range = 0.01 smu.source.vlimit.level = 1 smu.source.readback = smu.ON "
        f"smu.source.level = {level} "
        "smu.measure.func = smu.FUNC_DC_VOLTAGE smu.measure.sense = smu.SENSE_4WIRE "
        "smu.measure.autorange = smu.OFF smu.measure.range = 2 smu.measure.nplc = 1 "
    )


def cv(level: float) -> str:
    """Voltage source (2 V range, 5 mA limit), then measure I on 10 mA."""
    return (
        "smu.source.func = smu.FUNC_DC_VOLTAGE smu.source.autorange = smu.OFF "
        "smu.source.range = 2 smu.source.ilimit.level = 0.005 smu.source.readback = smu.ON "
        f"smu.source.level = {level} "
        "smu.measure.func = smu.FUNC_DC_CURRENT smu.measure.sense = smu.SENSE_4WIRE "
        "smu.measure.autorange = smu.OFF smu.measure.range = 0.01 smu.measure.nplc = 1 "
    )


def store(name: str) -> str:
    return f'smu.source.configlist.store("S{name}") smu.measure.configlist.store("M{name}") '


# Per variant: config entries, then trigger-model blocks between busy-on and busy-off.
VARIANTS = {
    "A": ([cc(0.004), cv(0.3)], ["recall 1", "on", "measure", "recall 2", "measure"]),
    "B": ([cc(0.004), cv(0.3)], ["recall 1", "on", "measure", "recall 2", "on", "measure"]),
    "C": (
        [cc(0.004), cc(0), cc(-0.002)],
        ["recall 1", "on", "measure", "recall 2", "measure", "recall 3", "measure"],
    ),
}


def block(name: str, step: str) -> str:
    if step == "on":
        return "trigger.BLOCK_SOURCE_OUTPUT, smu.ON"
    if step == "measure":
        return f"trigger.BLOCK_MEASURE_DIGITIZE, oe_e2, {COUNT}"
    index = int(step.split()[1])
    return f'trigger.BLOCK_CONFIG_RECALL, "S{name}", {index}, "M{name}", {index}'


def cleanup(t: PyVisaKeithley2460Transport) -> None:
    deletes = " ".join(
        f"pcall(smu.source.configlist.delete, 'S{name}') "
        f"pcall(smu.measure.configlist.delete, 'M{name}')"
        for name in VARIANTS
    )
    q(
        t,
        guarded(
            "if oe_e2 ~= nil then buffer.delete(oe_e2) oe_e2 = nil end "
            f"if oe_e2_lists then {deletes} oe_e2_lists = nil end "
            + cc(0)
            + 'trigger.model.load("Empty") return 1'
        ),
    )


def shutdown(t: PyVisaKeithley2460Transport) -> str:
    w(t, "trigger.model.abort()")
    w(t, "smu.source.output = smu.OFF")
    time.sleep(0.2)
    q(t, guarded("digio.line[2].state = 0 return 1"))
    return q(t, "print(smu.source.output)")


def run_variant(t: PyVisaKeithley2460Transport, name: str) -> dict[str, str]:
    entries, steps = VARIANTS[name]
    result: dict[str, str] = {}
    create = (
        f'smu.source.configlist.create("S{name}") smu.measure.configlist.create("M{name}") '
        "oe_e2_lists = true return 1"
    )
    q(t, guarded(create))
    for index, entry in enumerate(entries, start=1):
        result[f"entry_{index}"] = q(t, guarded(entry + store(name) + "return 1"))
        if not result[f"entry_{index}"].startswith("OK"):
            raise SystemExit(f"STOP: variant {name} entry {index} failed before arming")
    # Leave safe active settings (0 A) while arming.
    q(t, guarded(cc(0) + "return 1"))
    blocks = (
        ["trigger.BLOCK_DIGITAL_IO, 2, 2"]
        + [block(name, step) for step in steps]
        + ["trigger.BLOCK_SOURCE_OUTPUT, smu.OFF", "trigger.BLOCK_DIGITAL_IO, 0, 2"]
    )
    setblocks = [
        f"trigger.model.setblock({index}, {args})" for index, args in enumerate(blocks, start=1)
    ]
    half = len(setblocks) // 2
    # Short input lines: the instrument rejects long ones ("Input buffer overrun").
    result["model"] = q(
        t,
        guarded(
            "if oe_e2 ~= nil then buffer.delete(oe_e2) end "
            f"oe_e2 = buffer.make({4 * COUNT}, buffer.STYLE_STANDARD) "
            f'trigger.model.load("Empty") {" ".join(setblocks[:half])} return 1'
        ),
    )
    if result["model"].startswith("OK"):
        result["model"] = q(t, guarded(f"{' '.join(setblocks[half:])} return 1"))
    if not result["model"].startswith("OK"):
        raise SystemExit(f"STOP: variant {name} model setup failed before arming")
    require_idle_off(t)
    w(t, "trigger.model.initiate()")
    started = time.monotonic()
    while True:
        time.sleep(0.2)
        state = q(t, "print(trigger.model.state())")
        if "RUNNING" not in state and "WAITING" not in state:
            break
        if time.monotonic() - started > WATCHDOG_S:
            result["watchdog"] = state
            break
    result["model_state"] = state
    result["output_attr_after_model"] = q(t, "print(smu.source.output)")
    result["readout"] = q(
        t,
        guarded(
            "local r = '' for i = 1, oe_e2.n do "
            "r = r .. string.format('%.6f', oe_e2.relativetimestamps[i]) .. ',' "
            ".. string.format('%.7g', oe_e2.readings[i]) .. ',' "
            ".. string.format('%.7g', oe_e2.sourcevalues[i]) .. ',' "
            ".. tostring(oe_e2.sourcestatuses[i]) .. ';' end return r"
        ),
    )
    result["output_final"] = shutdown(t)
    if result["output_final"] != "smu.OFF":
        # Seen after E1: the attribute stays ON until a function change.
        result["output_after_function_toggle"] = q(
            t,
            guarded(
                "smu.source.func = smu.FUNC_DC_VOLTAGE smu.source.func = smu.FUNC_DC_CURRENT "
                "return smu.source.output"
            ),
        )
    return result


def summarize(name: str, readout: str) -> list[str]:
    rows = [r.split(",") for r in readout.removeprefix("OK ").split(";") if r.count(",") == 3]
    lines = []
    for phase in range(len(rows) // COUNT):
        chunk = rows[phase * COUNT : (phase + 1) * COUNT]
        statuses = sorted({int(float(row[3])) for row in chunk})
        on = all(s & STAT_OUTPUT for s in statuses)
        limit = any(s & STAT_LIMIT for s in statuses)
        mean = sum(float(row[1]) for row in chunk) / len(chunk)
        source = sum(float(row[2]) for row in chunk) / len(chunk)
        lines.append(
            f"{name} phase {phase + 1}: reading {mean:.5g}, source {source:.5g}, "
            f"output {'ON' if on else 'OFF/mixed'}, limit {'YES' if limit else 'no'}, "
            f"statuses {statuses}, t {chunk[0][0]}..{chunk[-1][0]} s"
        )
    for phase in range(1, len(rows) // COUNT):
        gap = float(rows[phase * COUNT][0]) - float(rows[phase * COUNT - 1][0])
        spacing = float(rows[1][0]) - float(rows[0][0])
        lines.append(f"{name} gap before phase {phase + 1}: {gap:.4f} s (spacing {spacing:.4f} s)")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resource", default=RESOURCE)
    parser.add_argument(
        "--i-confirm-100-ohm-four-wire-resistor", action="store_true", required=True
    )
    args = parser.parse_args()
    results: dict[str, object] = {}

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
        for name in VARIANTS:
            print(f"Variant {name}")
            results[name] = run_variant(t, name)
            if results[name]["output_final"] != "smu.OFF" and (
                results[name].get("output_after_function_toggle") != "OK smu.OFF"
            ):
                raise SystemExit(f"STOP: output not confirmed OFF after variant {name}")
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

    summary = [
        line
        for name in VARIANTS
        if isinstance(results.get(name), dict)
        for line in summarize(name, results[name]["readout"])  # type: ignore[index]
    ]
    print("\n".join(summary))
    print(f"OUTPUT FINAL: {results['output_final']}")
    stamp = datetime.now(UTC)
    out = Path("docs/evidence") / f"k2460-{stamp:%Y-%m-%d}-function-switch-e2.json"
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
