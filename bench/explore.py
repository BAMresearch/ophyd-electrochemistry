"""Exploration series on a coin cell: every run is checked against the authorization.

Each series has a page with its limits and stop rules (``SERIES``: b2 is
bench/plan-b2-explore.md, b3 bench/plan-b3-explore.md). The operator runs
``authorize`` once; the agent then uses ``check`` and ``run``. ``run`` refuses
anything outside the limits, runs one row, and exits non-zero on a stop rule.

    uv run --locked --extra experiment python bench/explore.py --series b3 authorize
    uv run --locked --extra experiment python bench/explore.py --series b3 check '{...}'
    uv run --locked --extra experiment python bench/explore.py --series b3 run '{...}'
"""

import argparse
import datetime
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ophyd_electrochemistry.sequences.queueserver import load_session, validate_rows
from ophyd_electrochemistry.sequences.run_file import read_run_file
from ophyd_electrochemistry.sequences.run_sheet import write_run_sheet

REPO = Path(__file__).parents[1]
BIN = REPO / ".venv/bin"


@dataclass(frozen=True)
class Series:
    """The limits of one authorized series; its page in bench/ says why."""

    page: str
    session: Path
    sample: str
    i_max_a: float  # pulses shorter than long_pulse_s
    i_max_long_a: float  # pulses of long_pulse_s and longer
    long_pulse_s: float
    pulse_s: tuple[float, float]
    rest_s: tuple[float, float]
    run_charge_uah: float
    net_uah: tuple[float, float]  # allowed net charge over the series, + = desodiation
    v_high: float  # default upper cutoff of current rows
    v_low: float | None  # default lower cutoff of current rows


SERIES = {
    "b2": Series(
        page="bench/plan-b2-explore.md",
        session=REPO / "bench/b2/session-cell-explore.toml",
        sample="Richard-HC400-rev",
        i_max_a=0.0192,  # 40C of the assumed 0.48 mAh
        i_max_long_a=0.0192,
        long_pulse_s=1.0,
        pulse_s=(0.05, 60.0),
        rest_s=(1.0, 600.0),
        run_charge_uah=60.0,
        net_uah=(-40.0, 40.0),
        v_high=2.9,
        v_low=None,
    ),
    "b3": Series(
        page="bench/plan-b3-explore.md",
        session=REPO / "bench/b3/session-b3-explore.toml",
        sample="AlCe Test Cell 1",
        i_max_a=0.0104,  # 40C of the measured 0.26 mAh, pulses under 1 s only
        i_max_long_a=0.0013,  # 5C from 1 s on
        long_pulse_s=1.0,
        pulse_s=(0.05, 120.0),
        rest_s=(1.0, 600.0),
        run_charge_uah=80.0,
        net_uah=(-15.0, 150.0),  # starts sodiated: little further sodiation
        v_high=2.9,
        v_low=-2.9,
    ),
}
CFG = SERIES["b2"]  # main() selects the series

MAX_BLOCKS = 63  # firmware 1.7.16a; kept as a second check of the compiler default
SERIES_S = 7200.0
REFERENCE_FACTOR = 2.0  # stop if the reference 1 s resistance changes by more
REFERENCE_DV = 0.05  # compare references only within this pre-pulse voltage
SHORT_OHM = 20.0  # stop if a pulse sees less than this
REST_A = 1e-6

STOP_OK, STOP_RULE, STOP_UNSURE = 0, 3, 4


class Refused(Exception):
    """The row is outside the authorization; nothing was sent."""


def run_charge_uah(row: dict[str, Any]) -> tuple[float, float]:
    """(total moved, net) in uAh: all pulses, positive = desodiation."""
    if row["protocol"] == "rest":
        return 0.0, 0.0
    each = abs(row["current_a"]) * row["pulse_s"] / 3600 * 1e6
    if row["protocol"] == "bipolar_pulse":
        return 2 * each * row["repeats"], 0.0
    return each * row["repeats"], float(np.sign(row["current_a"])) * each * row["repeats"]


def check_row(row: dict[str, Any], net_uah: float, elapsed_s: float, nominal_s: float) -> None:
    """Raise Refused unless this row fits the limits given the series so far."""
    protocol = row.get("protocol")
    if protocol not in ("rest", "pulse_train", "bipolar_pulse"):
        raise Refused(f"protocol {protocol!r} is not in the authorization")
    if row.get("start_mode", "immediate") != "immediate":
        raise Refused("immediate start only")
    if row.get("sample_id", CFG.sample) != CFG.sample:
        raise Refused("another sample")
    rests = [row["rest_s"]] + ([row["pre_rest_s"]] if "pre_rest_s" in row else [])
    if not all(CFG.rest_s[0] <= r <= CFG.rest_s[1] for r in rests):
        raise Refused(f"rests {rests} outside {CFG.rest_s} s")
    if protocol != "rest":
        if not CFG.pulse_s[0] <= row["pulse_s"] <= CFG.pulse_s[1]:
            raise Refused(f"pulse {row['pulse_s']} s outside {CFG.pulse_s} s")
        cap = CFG.i_max_long_a if row["pulse_s"] >= CFG.long_pulse_s else CFG.i_max_a
        if abs(row["current_a"]) > cap:
            raise Refused(f"|I| {abs(row['current_a'])} A above {cap} A")
        if protocol == "bipolar_pulse" and row.get("start_with", "charge") != "charge":
            raise Refused("bipolar rows start with desodiation")
        # The page's cutoffs, not the wider session window (brief 14): prepare()
        # fills them in, so a missing or wider cutoff is refused here.
        if row.get("v_high") is None or row["v_high"] > CFG.v_high:
            raise Refused(f"v_high {row.get('v_high')} must be at most {CFG.v_high} V")
        if CFG.v_low is not None and (row.get("v_low") is None or row["v_low"] < CFG.v_low):
            raise Refused(f"v_low {row.get('v_low')} must be at least {CFG.v_low} V")
        total, net = run_charge_uah(row)
        if total > CFG.run_charge_uah:
            raise Refused(f"{total:.1f} uAh in one run, above {CFG.run_charge_uah}")
        each = abs(row["current_a"]) * row["pulse_s"] / 3600 * 1e6
        low, high = CFG.net_uah
        # bipolar: desodiation first, so each pair goes up by one pulse and back
        reach = [net_uah + net] if protocol == "pulse_train" else [net_uah + each, net_uah]
        if not all(low <= r <= high for r in reach):
            raise Refused(f"net charge would reach {reach} uAh, outside {CFG.net_uah}")
    if elapsed_s + 1.02 * nominal_s > SERIES_S:
        raise Refused(f"{elapsed_s + nominal_s:.0f} s would pass the {SERIES_S:.0f} s series limit")


def pulses(data: dict[str, Any]) -> list[dict[str, float]]:
    """Per pulse: voltages, apparent resistance at the second sample and at the end."""
    r = data["readout"]
    t, v, i = (np.asarray(r[k]) for k in ("t_s", "voltage_v", "current_a"))
    # Relative to the pulses: the first sample of a run can read a few uA at 0 A.
    on = np.abs(i) > max(REST_A, 0.2 * float(np.max(np.abs(i), initial=0.0)))
    rises = np.flatnonzero(np.diff(on.astype(int)) == 1) + 1
    falls = np.flatnonzero(np.diff(on.astype(int)) == -1) + 1
    rows = []
    for n, a in enumerate(rises, 1):
        later = falls[falls > a]
        b = int(later[0]) if len(later) else len(v)  # a cutoff ends the run in a pulse
        nxt = int(rises[n]) - 1 if n < len(rises) else len(v) - 1
        level = float(np.median(i[a:b]))
        pre = float(v[a - 1])
        rows.append(
            {
                "n": n,
                "t_s": float(t[a]),
                "i_a": level,
                "v_pre": pre,
                "v_first": float(v[a]),
                "v_end": float(v[b - 1]),
                "r_fast_ohm": float((v[min(a + 1, b - 1)] - pre) / level),
                "r_end_ohm": float((v[b - 1] - pre) / level),
                "i_error": float(np.max(np.abs(i[a + 1 : b] - level)) / abs(level))
                if b > a + 1
                else 0.0,
                "v_rest_end": float(v[nxt]),
            }
        )
    return rows


def assess(data: dict[str, Any], first_reference: float | None) -> list[str]:
    """The stop rules of the plan, as messages; empty means carry on."""
    problems = []
    outcome = data["run"]["outcome"]
    if outcome not in ("completed", "stopped_cutoff"):
        problems.append(f"outcome {outcome}")
    table = pulses(data)
    v = np.asarray(data["readout"]["voltage_v"])
    if len(v) and v[-1] < 0:
        problems.append(f"last rest voltage {v[-1]:.3f} V is negative")
    if len(v) and float(np.max(np.abs(data["readout"]["current_a"]))) > 1e-4 and not table:
        problems.append("current flowed but no pulse was found")
    for p in table:
        if not all(np.isfinite(x) for x in p.values()):
            problems.append(f"pulse {p['n']}: not finite, the stop rules cannot judge it")
        if abs(p["r_end_ohm"]) < SHORT_OHM:
            problems.append(f"pulse {p['n']}: {p['r_end_ohm']:.1f} ohm, a short?")
        if p["i_error"] > 0.02:
            problems.append(
                f"pulse {p['n']}: current off by {p['i_error']:.1%}, compliance or open?"
            )
    name = data["attrs"]["measurement_id"]
    if first_reference is not None and name.startswith("ref") and table:
        now = float(np.median([abs(p["r_end_ohm"]) for p in table]))
        ratio = max(now / first_reference, first_reference / now)
        if ratio > REFERENCE_FACTOR:
            problems.append(
                f"reference {now:.0f} ohm against {first_reference:.0f} ohm at the start"
            )
    return problems


def run_files(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.h5"), key=lambda p: p.name.rsplit("_", 1)[-1])


def net_charge_uah(directory: Path) -> float:
    total = 0.0
    for path in run_files(directory):
        r = read_run_file(path)["readout"]
        t, i = np.asarray(r["t_s"]), np.asarray(r["current_a"])
        total += float(np.trapezoid(np.where(np.abs(i) > REST_A, i, 0.0), t)) / 3600 * 1e6
    return total


def reference_ohm(
    directory: Path, direction: str, v_pre: float | None = None, exclude: Path | None = None
) -> float | None:
    """The first reference row of this direction (``ref-01d`` or ``ref-01s``).

    With ``v_pre`` (the newest reference's first pre-pulse voltage) only an
    earlier reference starting within REFERENCE_DV of it counts: the resistance
    depends on the state of charge, so references at other voltages are not
    comparable (6 Nov: the rule stopped on a low-voltage reference). ``exclude``
    is the newest run itself, which must not be its own baseline (brief 14).
    None means no comparable baseline: the caller reports it.
    """
    for path in run_files(directory):
        name = path.name.rsplit("_", 1)[0]
        if exclude is not None and path.resolve() == exclude.resolve():
            continue
        if name.startswith("ref") and name.endswith(direction):
            table = pulses(read_run_file(path))
            if table and (v_pre is None or abs(table[0]["v_pre"] - v_pre) <= REFERENCE_DV):
                return float(np.median([abs(p["r_end_ohm"]) for p in table]))
    return None


def qserver(*args: str) -> str:
    done = subprocess.run([BIN / "qserver", *args], capture_output=True, text=True, timeout=30)
    if done.returncode:
        raise RuntimeError(f"qserver {' '.join(args)}: {done.stdout}{done.stderr}")
    return done.stdout


def status() -> dict[str, str]:
    return dict(
        re.findall(
            r"'(manager_state|items_in_queue|running_item_uid)': '?([^',\n]+)", qserver("status")
        )
    )


def manager_session() -> str:
    table = subprocess.run(["ps", "eww", "-A"], capture_output=True, text=True).stdout
    for line in table.splitlines():
        if "bin/start-re-manager" in line and (m := re.search(r"OE_SESSION=(\S+)", line)):
            return m.group(1)
    return ""


def prepare(row: dict[str, Any], session: Any, series: dict[str, Any]) -> tuple[Path, float]:
    """Write the row's sheet after every check; returns it and its nominal duration."""
    row = {"sample_id": CFG.sample, **row}
    if row.get("protocol") != "rest":
        row.setdefault("v_high", CFG.v_high)
        if CFG.v_low is not None:
            row.setdefault("v_low", CFG.v_low)
    directory = session.data_dir / "sheets"
    directory.mkdir(parents=True, exist_ok=True)
    sheet = directory / f"{row['measurement_id']}.xlsx"
    if sheet.exists():
        raise Refused(f"{row['measurement_id']} was used already")
    write_run_sheet(sheet, [row], notes=[("Explore", CFG.page)])
    try:
        ((_, problem, summary),) = validate_rows(session, sheet)
        if problem:
            raise Refused(problem)
        h, m, s = map(int, re.search(r"(\d+):(\d\d):(\d\d)", summary).groups())
        nominal = 3600.0 * h + 60 * m + s
        blocks = int(re.search(r"(\d+) blocks", summary).group(1))
        if blocks > MAX_BLOCKS:
            raise Refused(f"{blocks} trigger blocks, above {MAX_BLOCKS}")
        started = datetime.datetime.fromisoformat(series["start_utc"])
        elapsed = (datetime.datetime.now(datetime.UTC) - started).total_seconds()
        check_row(row, net_charge_uah(session.data_dir), elapsed, nominal)
    except Exception:
        sheet.unlink()
        raise
    print(summary, f"| net {net_charge_uah(session.data_dir):+.2f} uAh, series {elapsed:.0f} s")
    return sheet, nominal


def run(row: dict[str, Any], session: Any, series: dict[str, Any]) -> int:
    if Path(manager_session()).resolve() != CFG.session:
        raise Refused(f"the RE Manager runs {manager_session()!r}, not the explore session")
    state = status()
    if state.get("manager_state") != "idle" or state.get("items_in_queue") != "0":
        raise Refused(f"manager not idle with an empty queue: {state}")
    sheet, nominal = prepare(row, session, series)
    name = row["measurement_id"]
    done = subprocess.run(
        [BIN / "oe-enqueue", sheet, name, "--session", CFG.session],
        input=f"{name}\n",
        capture_output=True,
        text=True,
        timeout=30,
    )
    if done.returncode:
        print(done.stdout, done.stderr)
        return STOP_UNSURE
    qserver("queue", "start")
    deadline = time.monotonic() + nominal * 1.05 + 120
    time.sleep(3)
    while (state := status()).get("manager_state") != "idle":
        if time.monotonic() > deadline:
            print(f"Still running past the expected end: {state}")
            return STOP_UNSURE
        time.sleep(5)
    if state.get("items_in_queue") != "0":
        print(f"The item returned to the queue (failed): {state}")
        return STOP_UNSURE
    path = next(
        reversed([p for p in run_files(session.data_dir) if p.name.startswith(f"{name}_")]), None
    )
    if path is None:
        print("No run file")
        return STOP_UNSURE
    data = read_run_file(path)
    table = pulses(data)
    v_pre = table[0]["v_pre"] if table else None
    baseline = reference_ohm(session.data_dir, name[-1], v_pre, exclude=path)
    if name.startswith("ref") and baseline is None:
        print(f"No earlier reference within {REFERENCE_DV} V of {v_pre}: no factor-2 check")
    problems = assess(data, baseline)
    entry = {
        "utc": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "row": row,
        "file": path.name,
        "outcome": data["run"]["outcome"],
        "pulses": pulses(data),
        "stop": problems,
    }
    with (session.data_dir / "series.jsonl").open("a") as stream:
        stream.write(json.dumps(entry) + "\n")
    table = entry["pulses"]
    print(path.name, entry["outcome"], f"{len(table)} pulses")
    for p in table[:2] + table[-2:] if len(table) > 4 else table:
        print({k: round(x, 4) for k, x in p.items()})
    for problem in problems:
        print("STOP:", problem)
    return STOP_RULE if problems else STOP_OK


def authorize(session: Any, series_path: Path) -> int:
    print(f"Series page {CFG.page}; session {CFG.session.relative_to(REPO)}.")
    print(f"Cell {CFG.sample}: HC on HI (force and sense), Na on LO, output-off HIGH-Z.")
    print(
        f"Window {session.limits.v_min_abs} to {session.limits.v_max_abs} V; rows stop at "
        f"{CFG.v_low} / {CFG.v_high} V."
    )
    print(f"|I| <= {CFG.i_max_a} A under {CFG.long_pulse_s} s, <= {CFG.i_max_long_a} A from it.")
    print(f"Pulses {CFG.pulse_s} s, rests {CFG.rest_s} s, {CFG.run_charge_uah} uAh per run,")
    started = json.loads(series_path.read_text())["start_utc"] if series_path.exists() else None
    print(f"net {CFG.net_uah} uAh, {SERIES_S / 3600:g} h in total, from {started or 'now'}.")
    if input(f"Type {CFG.sample!r} to authorize this series: ").strip() != CFG.sample:
        print("Not authorized")
        return 1
    now = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")
    series_path.write_text(json.dumps({"start_utc": started or now, "confirmed_utc": now}) + "\n")
    print(f"Authorized at {now}; the series clock started at {started or now}")
    return 0


def main(argv: list[str] | None = None) -> int:
    global CFG
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--series", choices=sorted(SERIES), default="b2")
    parser.add_argument("command", choices=("authorize", "check", "run"))
    parser.add_argument("row", nargs="?", help="the run-sheet row as JSON")
    args = parser.parse_args(argv)
    CFG = SERIES[args.series]
    session = load_session(CFG.session)
    session.data_dir.mkdir(parents=True, exist_ok=True)
    series_path = session.data_dir / "series.json"
    if args.command == "authorize":
        return authorize(session, series_path)
    if not series_path.exists():
        print("Refused: the operator has not run authorize", file=sys.stderr)
        return 2
    series = json.loads(series_path.read_text())
    row = json.loads(args.row)
    try:
        if args.command == "check":
            sheet, _ = prepare(row, session, series)
            sheet.unlink()
            return STOP_OK
        return run(row, session, series)
    except Refused as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
