"""B2: write the resistor and coin-cell run sheets and validate them offline.

Currents scale with the cell capacity (1C = capacity / 1 h). Values are bench
placeholders, not the beamtime protocol. Plan: ``bench/plan-b2.md``.

    uv run --locked --extra experiment python bench/b2_sheets.py --capacity-mah 0.6 \\
        --sample-id HC-coin-01
"""

import argparse
from pathlib import Path

from ophyd_electrochemistry.sequences.queueserver import load_session, validate_rows
from ophyd_electrochemistry.sequences.run_sheet import write_run_sheet

HERE = Path(__file__).parent / "b2"
I_RESISTOR = 4.5e-4  # beamtime-like 1C pulse; 45 mV on 100 ohm

# A sheet is loaded whole against one session's limits, so one sheet per session.
SHEETS = {
    "b2-resistor.xlsx": "session-resistor.toml",
    "b2-cell-wide.xlsx": "session-cell-wide.toml",
    "b2-cell.xlsx": "session-cell.toml",
    "b2-cell-cycle.xlsx": "session-cell-cycle.toml",
    "b2-cell-pulse.xlsx": "session-cell-wide.toml",
}


def resistor_rows() -> list[dict[str, object]]:
    pulse = dict(
        protocol="bipolar_pulse",
        current_a=I_RESISTOR,
        pulse_s=1,
        rest_s=1,
        fast_window_s=1,  # one sampling phase per step: 59 trigger blocks (limit 63)
        pre_rest_s=5,
        v_low=-0.2,
        v_high=0.2,
        start_with="discharge",
    )
    rows: list[dict[str, object]] = [
        dict(measurement_id="b2r-rest", protocol="rest", rest_s=10, interval_s=0.1),
        dict(measurement_id="b2r-pulse", repeats=20, **pulse),
        dict(measurement_id="b2r-pulse-ext", repeats=3, start_mode="external", **pulse),
        # -45 mV is past -30 mV at the first sample: the cutoff ends the CC step.
        dict(
            measurement_id="b2r-soc",
            protocol="soc_position",
            current_a=-I_RESISTOR,
            target_v=-0.03,
            max_step_s=60,
            rest_s=10,
            interval_s=0.1,
        ),
        # Every CC step cuts at once; the CV hold at -30 mV draws 0.3 mA < 0.5 mA,
        # so its taper fires at once too: I->V->I switches inside one model.
        # (One cycle: the nominal duration counts the 1 h CV maximum.)
        dict(
            measurement_id="b2r-cycle",
            protocol="cycling",
            current_a=I_RESISTOR,
            v_low=-0.03,
            v_high=0.03,
            cycles=1,
            start_with="discharge",
            cv_i_abs_min_a=5e-4,
            cv_after="discharge",
            rest_s=5,
            max_step_s=60,
            interval_s=0.1,
        ),
        dict(measurement_id="b2r-abort", repeats=300, **pulse),  # 20 min
        dict(measurement_id="b2r-crash", repeats=30, **pulse),  # 2 min
        # 30 min at 6 ms, ~300k records: the buffer size of the K4/K5 cell runs.
        dict(measurement_id="b2r-soak", repeats=450, **pulse),
    ]
    for row in rows:
        row["sample_id"] = "R100"
        row["notes"] = "B2 resistor, 100 ohm 4-wire front"
    return rows


RATES_C = (1, 5, 10, 20, 40)  # K2 rate check, stopped at the first cutoff


def cell_rows(
    capacity_mah: float, sample_id: str, pulse_c_rate: float = 10
) -> tuple[list[dict[str, object]], ...]:
    """(wide-window rows, pulse-day rows, reference-cycle rows).

    Protocol (4 Nov): start fully sodiated; unipolar 1 s pulses at a high rate
    with 60 s zero-current rests, in both directions, each run ending at its
    voltage limit (``repeats`` is only an upper bound, 1.5x the capacity).
    Negative current = sodiation.
    """
    c1 = capacity_mah * 1e-3  # A
    # 1 s pulses sampled at 6 ms throughout, and the first second of each rest.
    short = dict(protocol="pulse_train", pulse_s=1, fast_window_s=1)
    wide: list[dict[str, object]] = [
        dict(measurement_id="k1-ocv", protocol="rest", rest_s=120, interval_s=1),
    ]
    # Rate check: 3 pulses per rate, away from the plating region either way.
    for direction, sign, limit in (("sod", -1, dict(v_low=0.05)), ("desod", 1, dict(v_high=2.9))):
        for rate in RATES_C:
            wide.append(
                dict(
                    measurement_id=f"k2-{direction}-{rate}C",
                    **short,
                    current_a=sign * rate * c1,
                    rest_s=20,
                    repeats=3,
                    pre_rest_s=10,
                    **limit,
                )
            )
    # Fully sodiated by morning: C/10 takes ~10 h for the whole capacity.
    wide.append(
        dict(
            measurement_id="k3-sodiate-5mV",
            protocol="soc_position",
            current_a=-c1 / 10,
            target_v=0.005,
            max_step_s=50400,
            rest_s=1800,
            interval_s=1,
        )
    )
    # Pulse day, from the fully sodiated state: desodiation to the upper limit,
    # then sodiation back to the lower one. Cutoffs are tested under load.
    bound = round(1.5 * 3600 / pulse_c_rate)
    train = dict(**short, rest_s=60, repeats=bound, pre_rest_s=60, interval_s=0.25)
    tag = f"{pulse_c_rate:g}C"
    measure: list[dict[str, object]] = [
        dict(measurement_id=f"k4-desod-1s-{tag}", **train, current_a=pulse_c_rate * c1, v_high=0.2),
        dict(measurement_id=f"k5-sod-1s-{tag}", **train, current_a=-pulse_c_rate * c1, v_low=0.005),
    ]
    # Weekend: non-pulsed reference, desodiation first, ending fully sodiated;
    # the session's 100 uA also bounds the CV hold.
    cycle: list[dict[str, object]] = [
        dict(
            measurement_id="k6-reference-cycle",
            protocol="cycling",
            current_a=c1 / 20,
            v_low=0.005,
            v_high=0.2,
            cycles=1,
            start_with="desodiation",
            cv_i_abs_min_a=c1 / 100,
            cv_after="sodiation",
            rest_s=1800,
            max_step_s=72000,  # C/20 passes the whole capacity in 20 h
            interval_s=1,
        ),
    ]
    for row in wide + measure + cycle:
        row["sample_id"] = sample_id
        row["notes"] = f"B2 coin cell, capacity {capacity_mah:g} mAh assumed (1C = {c1:.3g} A)"
    return wide, measure, cycle


def pulse_rows(capacity_mah: float, sample_id: str) -> list[dict[str, object]]:
    """K7 (6 Nov): 1C pulse trains on the high-impedance coin cell after K3.

    The desodiation train ends after its 60 pulses (the 2.9 V limit only guards
    the window); the sodiation train keeps the 5 mV limit under load and is
    expected to stop on an early pulse: the under-load end-condition problem.
    """
    c1 = capacity_mah * 1e-3
    train = dict(protocol="pulse_train", pulse_s=1, fast_window_s=1, rest_s=10, repeats=60)
    train.update(pre_rest_s=10, interval_s=0.25)
    rows: list[dict[str, object]] = [
        dict(measurement_id="k7-ocv", protocol="rest", rest_s=120, interval_s=1),
        dict(measurement_id="k7-desod-1C", **train, current_a=c1, v_high=2.9),
        dict(measurement_id="k7-sod-1C", **train, current_a=-c1, v_low=0.005),
    ]
    for row in rows:
        row["sample_id"] = sample_id
        row["notes"] = f"B2 K7 coin cell, capacity {capacity_mah:g} mAh assumed (1C = {c1:.3g} A)"
    return rows


def write(
    out: Path,
    capacity_mah: float,
    sample_id: str,
    pulse_c_rate: float = 10,
    *,
    overwrite: bool = False,
    only: tuple[str, ...] = (),
) -> int:
    """Write both sheets and validate every row against its session; 0 if all pass."""
    out.mkdir(parents=True, exist_ok=True)
    note = [("B2", "From bench/b2_sheets.py; run each sheet in its session (bench/plan-b2.md).")]
    rows = (
        resistor_rows(),
        *cell_rows(capacity_mah, sample_id, pulse_c_rate),
        pulse_rows(capacity_mah, sample_id),
    )
    failed = 0
    for (name, session), sheet_rows in zip(SHEETS.items(), rows, strict=True):
        if only and name not in only:
            continue
        path = out / name
        write_run_sheet(path, sheet_rows, notes=note, overwrite=overwrite)
        print(f"{path}  (OE_SESSION=bench/b2/{session})")
        for measurement_id, problem, summary in validate_rows(load_session(HERE / session), path):
            failed += problem is not None
            print(f"  {'FAIL' if problem else 'ok  '} {measurement_id}: {problem or summary}")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--capacity-mah", type=float, default=0.6, help="coin-cell capacity")
    parser.add_argument("--sample-id", default="HC-coin-01")
    parser.add_argument(
        "--pulse-c-rate", type=float, default=10, help="pulse-day rate (from the K2 rate check)"
    )
    parser.add_argument("--out", type=Path, default=Path.home() / "k2460-data" / "b2")
    parser.add_argument("--overwrite", action="store_true", help="replace existing sheets")
    parser.add_argument(
        "--only", nargs="+", default=(), choices=list(SHEETS), help="sheets to write"
    )
    args = parser.parse_args(argv)
    if not 0.01 <= args.capacity_mah <= 5:
        parser.error("--capacity-mah must be in 0.01..5 (a coin cell)")
    if not 5 <= args.pulse_c_rate <= 40:
        parser.error("--pulse-c-rate must be in 5..40 (lower rates exceed the record budget)")
    return write(
        args.out,
        args.capacity_mah,
        args.sample_id,
        args.pulse_c_rate,
        overwrite=args.overwrite,
        only=tuple(args.only),
    )


if __name__ == "__main__":
    raise SystemExit(main())
