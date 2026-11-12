"""B3: run sheets for a healthy hard-carbon/Na coin cell, validated offline.

Two sheets, one per session (bench/plan-b3.md):

- ``b3-p0.xlsx`` (``b3/session-b3-p0.toml``, 0-3 V, 200 uA): OCV, then P0 as one
  row the 2460 runs alone overnight (10 Nov, Jonas): C/5 sodiation to 5 mV with a
  CV hold to C/50, C/5 desodiation to 2.5 V, then C/5 sodiation to 5 mV again
  (CV, rest), so the cell ends sodiated for the pulse day. It is a ``cycling``
  row starting with desodiation: on a cell delivered at or above 2.5 V that first
  half ends at its first reading and its 30 min rest is the OCV.
- ``b3-ici.xlsx`` (``b3/session-b3-p0.toml``): resistance, diffusion slope and
  quasi-OCV against state of charge (ICI: Lacey 2017; Chien et al. 2023), on the
  measured capacity (``--ici-capacity-mah``, 0.26 mAh after P0, 12 Nov): C/2 for
  120 s, 10 s pauses, desodiation to 2.5 V, then sodiation to 5 mV under load.
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
    "b3-ici.xlsx": "session-b3-p0.toml",
    "b3-store.xlsx": "session-b3-p0.toml",
}
P0_C_RATE = 5  # C/5: about 5 h per half for the reversible capacity
P0_MAX_S = 8 * 3600  # per CC step: the first sodiation adds the irreversible loss
P0_V_LOW, P0_V_HIGH = 0.005, 2.5


def p0_rows(capacity_mah: float, sample_id: str) -> list[dict[str, object]]:
    """OCV, then P0: C/5 to 5 mV (CV to C/50), C/5 to 2.5 V, C/5 to 5 mV (CV)."""
    c1 = capacity_mah * 1e-3
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
        # Two cycles starting with desodiation: [to 2.5 V, rest] [to 5 mV, CV, rest]
        # [to 2.5 V, rest] [to 5 mV, CV, rest]. The first half only tops the cell up
        # to 2.5 V (or ends at once), so the sequence is sodiate, desodiate,
        # sodiate, with no host step in between. The CV hold is at most 1 h; its
        # current limit is the session's i_max_abs.
        dict(
            measurement_id="hc-p0",
            protocol="cycling",
            current_a=c1 / P0_C_RATE,
            v_low=P0_V_LOW,
            v_high=P0_V_HIGH,
            cycles=2,
            start_with="desodiation",
            cv_i_abs_min_a=c1 / 50,
            cv_after="sodiation",
            rest_s=1800,
            max_step_s=P0_MAX_S,
            interval_s=1,
        ),
    ]
    for row in rows:
        row["sample_id"] = sample_id
        row["notes"] = (
            f"B3 P0 at C/5, capacity {capacity_mah:g} mAh assumed (1C = {c1:.3g} A): "
            f"to {P0_V_LOW:g} V (CV to C/50), to {P0_V_HIGH:g} V, to {P0_V_LOW:g} V (CV)"
        )
    return rows


def ici_rows(capacity_mah: float, sample_id: str) -> list[dict[str, object]]:
    """Full-range ICI at C/2: 120 s current, 10 s pauses, 6 ms for 1 s at each edge."""
    c1 = capacity_mah * 1e-3
    step = dict(
        protocol="pulse_train",
        pulse_s=120,
        rest_s=10,
        pre_rest_s=10,
        repeats=80,  # 4.3 uAh steps for 0.26 mAh: a 1.3x bound; the cutoff ends it
        fast_window_s=1,
        interval_s=1,
    )
    rows: list[dict[str, object]] = [
        dict(step, measurement_id="ici-full-d", current_a=c1 / 2, v_high=P0_V_HIGH),
        dict(step, measurement_id="ici-full-s", current_a=-c1 / 2, v_low=P0_V_LOW),
    ]
    for row in rows:
        row["sample_id"] = sample_id
        row["notes"] = (
            f"B3 ICI at C/2 on {capacity_mah:g} mAh (measured): 120 s on, 10 s pauses; "
            f"desodiation to {P0_V_HIGH:g} V, sodiation to {P0_V_LOW:g} V under load"
        )
    return rows


STORE_C_RATE = 20
STORE_MAX_S = 30 * 3600  # about 20 h for the reversible capacity at C/20


def store_rows(capacity_mah: float, sample_id: str) -> list[dict[str, object]]:
    """Storage: desodiate at C/20 until the voltage under load reaches 2.5 V, rest 30 min."""
    c1 = capacity_mah * 1e-3
    return [
        dict(
            measurement_id="store-d",
            protocol="soc_position",
            current_a=c1 / STORE_C_RATE,
            target_v=P0_V_HIGH,
            max_step_s=STORE_MAX_S,
            rest_s=1800,
            interval_s=10,
            sample_id=sample_id,
            notes=f"B3 storage: C/{STORE_C_RATE} on {capacity_mah:g} mAh (measured) to "
            f"{P0_V_HIGH:g} V under load, then 30 min rest; output OFF (HIGH-Z) at the end",
        )
    ]


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
        ici_rows(args.ici_capacity_mah, args.sample_id),
        store_rows(args.ici_capacity_mah, args.sample_id),
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
    parser.add_argument(
        "--ici-capacity-mah", type=float, default=0.26, help="measured capacity for the ICI sheet"
    )
    parser.add_argument("--rate-c", type=float, default=10, help="pulse rate (1 s pulses)")
    parser.add_argument("--desod-end-v", type=float, default=0.2, help="relaxed upper end")
    parser.add_argument("--sod-early-v", type=float, default=0.05, help="end of the 1 min row")
    parser.add_argument("--external", action="store_true", help="pulse rows wait for START")
    parser.add_argument("--out", type=Path, default=Path.home() / "k2460-data" / "b3")
    parser.add_argument("--overwrite", action="store_true", help="replace existing sheets")
    args = parser.parse_args(argv)
    # Also refuses NaN and negative values, which would flip the current signs (brief 14).
    for option in ("capacity_mah", "ici_capacity_mah"):
        if not 0.05 <= getattr(args, option) <= 1:
            parser.error(
                f"--{option.replace('_', '-')} must be in 0.05..1 (the sessions' 200 uA and 30 mA)"
            )
    if not 5 <= args.rate_c <= 40:
        parser.error("--rate-c must be in 5..40 (lower rates exceed the record budget)")
    # Checked before writing either sheet (Codex brief 12): currents within each session.
    c1 = args.capacity_mah * 1e-3
    for name, current in (
        ("b3-p0.xlsx", c1 / P0_C_RATE),
        ("b3-pulse.xlsx", args.rate_c * c1),
        ("b3-ici.xlsx", args.ici_capacity_mah * 1e-3 / 2),
        ("b3-store.xlsx", args.ici_capacity_mah * 1e-3 / STORE_C_RATE),
    ):
        i_max = load_session(HERE / SHEETS[name]).limits.i_max_abs
        if current > i_max:
            parser.error(
                f"{name}: {current * 1e3:.3g} mA exceeds its session's {i_max * 1e3:g} mA "
                "(lower --rate-c or --capacity-mah)"
            )
    return write(args.out, args)


if __name__ == "__main__":
    raise SystemExit(main())
