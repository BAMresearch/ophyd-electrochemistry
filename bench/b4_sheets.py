"""B4: weekend run sheet for the first two cycles of a new hard-carbon/Na coin cell.

One sheet, ``b4-weekend.xlsx``, for ``b4/session-b4-weekend.toml`` (0-3 V, 100 uA):

- ``b4-ocv``: 5 min rest (OCV; the first second at 6 ms records the output-ON
  transient on the 1 uA range). A cell above 3 V or reversed stops here.
- ``b4-cycles``: one row the 2460 runs alone, as two ``cycling`` cycles that
  start with sodiation (13 Nov): C/20 to 5 mV under load, CV at 5 mV until
  |I| < C/40 (at most 4 h), 30 min rest, C/20 to 2.5 V under load, 30 min rest;
  twice. No initial desodiation: the cell is new.

C-rates are on the nominal capacity: hard-carbon mass x 400 mAh/g (1.55 mg ->
0.62 mAh). Each CC step is bounded at 30 h (1.5x C/20: the first sodiation adds
the irreversible loss). 1 s sampling.

    uv run --locked --extra experiment python bench/b4_sheets.py --sample-id "Cell name"
"""

import argparse
import math
from pathlib import Path

from ophyd_electrochemistry.sequences.queueserver import load_session, validate_rows
from ophyd_electrochemistry.sequences.run_sheet import write_run_sheet

SESSION = Path(__file__).parent / "b4" / "session-b4-weekend.toml"
SPECIFIC_MAH_PER_G = 400
C_RATE, CV_END_C_RATE = 20, 40
V_LOW, V_HIGH = 0.005, 2.5
MAX_STEP_S = 30 * 3600
CV_MAX_S = 4 * 3600
REST_S = 1800


def weekend_rows(capacity_mah: float, sample_id: str) -> list[dict[str, object]]:
    c1 = capacity_mah * 1e-3
    note = (
        f"B4 weekend on {capacity_mah:g} mAh nominal (1C = {c1:.3g} A): twice "
        f"[C/{C_RATE} to {V_LOW:g} V, CV to C/{CV_END_C_RATE}, rest, C/{C_RATE} to "
        f"{V_HIGH:g} V, rest]"
    )
    rows: list[dict[str, object]] = [
        dict(
            measurement_id="b4-ocv",
            protocol="rest",
            rest_s=300,
            fast_interval_s=0.006,
            fast_window_s=1,
            interval_s=1,
            notes="B4: OCV of the new cell, 5 min",
        ),
        dict(
            measurement_id="b4-cycles",
            protocol="cycling",
            current_a=c1 / C_RATE,
            v_low=V_LOW,
            v_high=V_HIGH,
            cycles=2,
            start_with="sodiation",
            cv_after="sodiation",
            cv_i_abs_min_a=c1 / CV_END_C_RATE,
            cv_max_s=CV_MAX_S,
            rest_s=REST_S,
            max_step_s=MAX_STEP_S,
            interval_s=1,
            notes=note,
        ),
    ]
    for row in rows:
        row["sample_id"] = sample_id
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mass-mg", type=float, default=1.55, help="hard-carbon mass")
    parser.add_argument("--sample-id", required=True)
    parser.add_argument("--out", type=Path, default=Path.home() / "k2460-data" / "b4")
    parser.add_argument("--overwrite", action="store_true", help="replace an existing sheet")
    args = parser.parse_args(argv)
    if not (math.isfinite(args.mass_mg) and 0.2 <= args.mass_mg <= 5):
        parser.error("--mass-mg must be in 0.2..5")
    capacity_mah = args.mass_mg * SPECIFIC_MAH_PER_G * 1e-3
    session = load_session(SESSION)
    if capacity_mah * 1e-3 / C_RATE > session.limits.i_max_abs:
        parser.error("C/20 exceeds the session's i_max_abs")
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / "b4-weekend.xlsx"
    note = [("B4", "From bench/b4_sheets.py; session bench/b4/session-b4-weekend.toml.")]
    write_run_sheet(
        path, weekend_rows(capacity_mah, args.sample_id), notes=note, overwrite=args.overwrite
    )
    print(f"{path}  (session {SESSION.relative_to(Path.cwd())})")
    failed = 0
    for measurement_id, problem, summary in validate_rows(session, path):
        failed += problem is not None
        print(f"  {'FAIL' if problem else 'ok  '} {measurement_id}: {problem or summary}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
