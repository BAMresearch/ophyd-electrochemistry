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
}


def resistor_rows() -> list[dict[str, object]]:
    pulse = dict(
        protocol="bipolar_pulse",
        current_a=I_RESISTOR,
        pulse_s=1,
        rest_s=4,
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
        dict(measurement_id="b2r-abort", repeats=120, **pulse),
        dict(measurement_id="b2r-crash", repeats=12, **pulse),
        dict(measurement_id="b2r-soak", repeats=360, **pulse),
    ]
    for row in rows:
        row["sample_id"] = "R100"
        row["notes"] = "B2 resistor, 100 ohm 4-wire front"
    return rows


def cell_rows(capacity_mah: float, sample_id: str) -> tuple[list[dict[str, object]], ...]:
    """(wide-window rows, measurement-window rows, reference-cycle rows).

    Protocol as agreed with the material scientists on 4 Nov: start fully
    sodiated, unipolar pulses (no change of direction between pulses), 1C
    pulses of 10 s to 1 min, CC at C/20. Negative current = sodiation.
    """
    c1 = capacity_mah * 1e-3  # A
    train = dict(protocol="pulse_train", pre_rest_s=60)
    wide: list[dict[str, object]] = [
        dict(measurement_id="k1-ocv", protocol="rest", rest_s=120, interval_s=1),
        # Sodiation pulses near 2.5 V: no plating risk; the voltage must fall (polarity).
        dict(
            measurement_id="k2-pulse-check",
            **{**train, "pre_rest_s": 10},
            current_a=-c1,
            pulse_s=1,
            rest_s=4,
            repeats=5,
            v_low=0.05,
        ),
        # Fully sodiated by morning: C/10 takes ~10 h for the whole capacity.
        dict(
            measurement_id="k3-sodiate-5mV",
            protocol="soc_position",
            current_a=-c1 / 10,
            target_v=0.005,
            max_step_s=50400,
            rest_s=1800,
            interval_s=1,
        ),
    ]
    # Next bench day, from the fully sodiated state: the beamtime day in small.
    # Each train passes 1/6 of the capacity (10 min of 1C) in about 1 h.
    measure: list[dict[str, object]] = [
        dict(
            measurement_id="k4-desod-pulse-10s",
            **train,
            current_a=c1,
            pulse_s=10,
            rest_s=50,
            repeats=60,
            v_high=0.2,
        ),
        dict(
            measurement_id="k5-desod-pulse-60s",
            **train,
            current_a=c1,
            pulse_s=60,
            rest_s=300,
            repeats=10,
            v_high=0.2,
        ),
        # Sodiation pulses back toward 5 mV (stop there under load): plating risk.
        dict(
            measurement_id="k6-sod-pulse-10s",
            **train,
            current_a=-c1,
            pulse_s=10,
            rest_s=50,
            repeats=60,
            v_low=0.005,
        ),
    ]
    # Weekend: non-pulsed reference, desodiation first (from ~1/6 desodiated),
    # ending fully sodiated again; the session's 100 uA also bounds the CV hold.
    cycle: list[dict[str, object]] = [
        dict(
            measurement_id="k8-reference-cycle",
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


def write(out: Path, capacity_mah: float, sample_id: str, *, overwrite: bool = False) -> int:
    """Write both sheets and validate every row against its session; 0 if all pass."""
    out.mkdir(parents=True, exist_ok=True)
    note = [("B2", "From bench/b2_sheets.py; run each sheet in its session (bench/plan-b2.md).")]
    rows = (resistor_rows(), *cell_rows(capacity_mah, sample_id))
    failed = 0
    for (name, session), sheet_rows in zip(SHEETS.items(), rows, strict=True):
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
    parser.add_argument("--out", type=Path, default=Path.home() / "k2460-data" / "b2")
    parser.add_argument("--overwrite", action="store_true", help="replace existing sheets")
    args = parser.parse_args(argv)
    if not 0.01 <= args.capacity_mah <= 5:
        parser.error("--capacity-mah must be in 0.01..5 (a coin cell)")
    return write(args.out, args.capacity_mah, args.sample_id, overwrite=args.overwrite)


if __name__ == "__main__":
    raise SystemExit(main())
