"""B3: run sheets for a healthy hard-carbon/Na coin cell, validated offline.

Two sheets, one per session (bench/plan-b3.md):

- ``b3-p0.xlsx`` (``b3/session-b3-p0.toml``, 0-3 V, 100 uA): OCV, then P0, the
  overnight sodiation at C/10 to 5 mV with a 30 min rest; ``hc-p0-cv`` adds a
  CV hold at 5 mV instead (open question 3 in docs/beamtime-concept.md).
- ``b3-pulse.xlsx`` (``b3/session-b3-pulse.toml``, -3..3 V, 30 mA): the beamtime
  protocol in small, a draft until Jonas answers the open questions:
  desodiation pulses ending on the relaxed voltage (P2d), then sodiation pulses
  in two rows, 1 min rests to an early relaxed limit, 5 min rests to 5 mV (P2s).

Currents scale with the capacity (1C = capacity / 1 h); 1 s pulses at
``--rate-c``. Repeat counts are bounds (1.5x the capacity); the relaxed limit
ends each pulse row.

    uv run --locked --extra experiment python bench/b3_sheets.py --capacity-mah 0.6 \\
        --sample-id HC-BAM-01
"""

import argparse
import math
from pathlib import Path

from ophyd_electrochemistry.sequences.queueserver import load_session, validate_rows
from ophyd_electrochemistry.sequences.run_sheet import write_run_sheet

HERE = Path(__file__).parent / "b3"
SHEETS = {
    "b3-p0.xlsx": "session-b3-p0.toml",
    "b3-pulse.xlsx": "session-b3-pulse.toml",
}
P0_C_RATE = 10  # C/10: about 10 h from 2.5 V for the whole capacity
P0_MAX_S = 14 * 3600


def p0_rows(capacity_mah: float, sample_id: str) -> list[dict[str, object]]:
    """OCV, then CC sodiation at C/10 to 5 mV and a 30 min rest (P0)."""
    c1 = capacity_mah * 1e-3
    sodiate = dict(
        protocol="soc_position",
        current_a=-c1 / P0_C_RATE,
        target_v=0.005,
        max_step_s=P0_MAX_S,
        rest_s=1800,
        interval_s=1,
    )
    rows: list[dict[str, object]] = [
        # The first second at 6 ms records the output-ON transient (1 uA-range start).
        dict(
            measurement_id="hc-ocv",
            protocol="rest",
            rest_s=300,
            fast_interval_s=0.006,
            fast_window_s=1,
            interval_s=1,
        ),
        dict(sodiate, measurement_id="hc-p0"),
        # Alternative: hold 5 mV until |I| < C/50 (at most 2 h); the session's
        # 100 uA caps the hold current.
        dict(
            sodiate,
            measurement_id="hc-p0-cv",
            cv_i_abs_min_a=c1 / 50,
            cv_max_s=7200,
        ),
    ]
    for row in rows:
        row["sample_id"] = sample_id
        row["notes"] = f"B3 P0, capacity {capacity_mah:g} mAh assumed (1C = {c1:.3g} A)"
    return rows


def pulse_rows(
    capacity_mah: float,
    sample_id: str,
    rate_c: float,
    *,
    desod_end_v: float,
    sod_early_v: float,
    external: bool,
) -> list[dict[str, object]]:
    """OCV, desodiation pulses to a relaxed upper limit, sodiation in two rows."""
    c1 = capacity_mah * 1e-3
    bound = math.ceil(1.5 * 3600 / rate_c)  # pulses for 1.5x the capacity
    pulse = dict(
        protocol="pulse_train",
        pulse_s=1,
        pre_rest_s=10,
        fast_window_s=1,
        interval_s=0.25,
        start_mode="external" if external else "immediate",
    )
    rows: list[dict[str, object]] = [
        dict(
            measurement_id="p-ocv",
            protocol="rest",
            rest_s=300,
            fast_interval_s=0.006,
            fast_window_s=1,
            interval_s=1,
        ),
        dict(
            pulse,
            measurement_id="p2d-desod",
            current_a=rate_c * c1,
            rest_s=60,
            repeats=bound,
            rest_end_v=desod_end_v,
        ),
        dict(
            pulse,
            measurement_id="p2s-sod-1min",
            current_a=-rate_c * c1,
            rest_s=60,
            repeats=bound,
            rest_end_v=sod_early_v,
        ),
        dict(
            pulse,
            measurement_id="p2s-sod-5min",
            current_a=-rate_c * c1,
            rest_s=300,
            repeats=60,  # 5 h at most; the 5 mV relaxed limit ends it sooner
            rest_end_v=0.005,
        ),
    ]
    for row in rows:
        row["sample_id"] = sample_id
        row["notes"] = (
            f"B3 draft beamtime protocol, {rate_c:g}C, capacity {capacity_mah:g} mAh "
            f"(1C = {c1:.3g} A); desodiation end {desod_end_v:g} V, early sodiation "
            f"end {sod_early_v:g} V (to confirm with Jonas)"
        )
    return rows


def write(out: Path, args: argparse.Namespace) -> int:
    """Write both sheets and validate every row against its session; 0 if all pass."""
    out.mkdir(parents=True, exist_ok=True)
    note = [("B3", "From bench/b3_sheets.py; run each sheet in its session (bench/plan-b3.md).")]
    rows = (
        p0_rows(args.capacity_mah, args.sample_id),
        pulse_rows(
            args.capacity_mah,
            args.sample_id,
            args.rate_c,
            desod_end_v=args.desod_end_v,
            sod_early_v=args.sod_early_v,
            external=args.external,
        ),
    )
    failed = 0
    for (name, session), sheet_rows in zip(SHEETS.items(), rows, strict=True):
        path = out / name
        write_run_sheet(path, sheet_rows, notes=note, overwrite=args.overwrite)
        print(f"{path}  (session bench/b3/{session})")
        for measurement_id, problem, summary in validate_rows(load_session(HERE / session), path):
            failed += problem is not None
            print(f"  {'FAIL' if problem else 'ok  '} {measurement_id}: {problem or summary}")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--capacity-mah", type=float, default=0.6, help="cell capacity")
    parser.add_argument("--sample-id", default="HC-coin-B3")
    parser.add_argument("--rate-c", type=float, default=10, help="pulse rate (1 s pulses)")
    parser.add_argument("--desod-end-v", type=float, default=0.2, help="relaxed upper end")
    parser.add_argument("--sod-early-v", type=float, default=0.05, help="end of the 1 min row")
    parser.add_argument("--external", action="store_true", help="pulse rows wait for START")
    parser.add_argument("--out", type=Path, default=Path.home() / "k2460-data" / "b3")
    parser.add_argument("--overwrite", action="store_true", help="replace existing sheets")
    args = parser.parse_args(argv)
    if not 0.05 <= args.capacity_mah <= 1:
        parser.error("--capacity-mah must be in 0.05..1 (the sessions' 100 uA and 30 mA)")
    if not 5 <= args.rate_c <= 40:
        parser.error("--rate-c must be in 5..40 (lower rates exceed the record budget)")
    return write(args.out, args)


if __name__ == "__main__":
    raise SystemExit(main())
