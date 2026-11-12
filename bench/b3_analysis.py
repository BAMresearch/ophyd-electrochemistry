"""B3 analysis: ICI pauses, pulse overpotentials and the PDF reports of 12 Nov.

Reads run files (HDF5) or `oe-archive-retained` npz archives; never touches the
instrument. The helpers need numpy only; the reports also need matplotlib (and
scipy for the Butler-Volmer fit), which are not project dependencies:

    uv run --locked --extra experiment python bench/b3_analysis.py table FILE ...
    uv run --locked --extra experiment --with matplotlib --with scipy \\
        python bench/b3_analysis.py series-report ~/k2460-data/b3/explore SUMMARY.txt OUT.pdf
    uv run --locked --extra experiment --with matplotlib \\
        python bench/b3_analysis.py ici-report D_FILE S_FILE SUMMARY.txt OUT.pdf --npz-current-mode

ICI (Lacey 2017; Chien et al. 2023): per current interruption, k is the slope
of V against sqrt(t) over 0.1-1 s of the pause, divided by -I, and the voltage
at the end of the pause a quasi-OCV. Two resistances: R_first, the step from
the last reading under load to the first reading of the pause (6 ms later; it
includes the diffusion part I k sqrt(6 ms) and anything that relaxes within
6 ms), and R0, the papers' definition, from the intercept of the sqrt(t) fit
at t = 0. Times are from the last reading under load, which can precede the
switch by up to one sample (biases k up by about 1 % at 6 ms; Codex, brief 14).
"""

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from ophyd_electrochemistry.sequences.run_file import read_run_file

ON_A = 1e-5  # |I| above this counts as a current step (the pulses here are >= 13 uA)
K_WINDOW_S = (0.1, 1.0)
UAH = 3.6e-3  # A s per uAh
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
NPZ_HELP = "confirm that npz archives hold current and rest steps only (no CV)"


def load(path: Path, npz_current_mode: bool = False) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Time (s from the first reading), voltage and current of a run file or npz archive.

    An npz archive has no source mode per record: in current and rest steps
    `reading` is the voltage and `source` the current, in CV steps the reverse.
    It is read only when the caller confirms (``npz_current_mode``) that every
    record of the archived run is current-mode (brief 14).
    """
    if path.suffix == ".npz":
        if not npz_current_mode:
            raise ValueError(
                f"{path.name}: an npz archive does not record the source mode; confirm "
                "that the run had no voltage (CV) steps with --npz-current-mode"
            )
        z = np.load(path)
        t = (z["seconds"] - z["seconds"][0]) + z["fraction"]
        return t - t[0], np.asarray(z["reading"], float), np.asarray(z["source"], float)
    readout = read_run_file(path)["readout"]
    t, v, i = (np.asarray(readout[k], dtype=float) for k in ("t_s", "voltage_v", "current_a"))
    return t, v, i


def edges(i: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Indices of the first reading of each current step (rises) and of each pause (falls)."""
    on = (np.abs(i) > ON_A).astype(int)
    rises = np.flatnonzero(np.diff(on) == 1) + 1
    falls = np.flatnonzero(np.diff(on) == -1) + 1
    return rises, falls


def charge_uah(t: np.ndarray, i: np.ndarray) -> np.ndarray:
    """Cumulative charge at each reading (rectangle rule), uAh; + is desodiation."""
    return np.concatenate(([0.0], np.cumsum(i[1:] * np.diff(t)) / UAH))


def ici_pauses(t: np.ndarray, v: np.ndarray, i: np.ndarray) -> np.ndarray:
    """One row per interruption: charge before it (uAh), V under load, R_first (ohm),
    k (ohm s^-0.5), V at the end of the pause, R0 (ohm, fit intercept). k and R0
    are NaN with three or fewer readings in the k window."""
    rises, falls = edges(i)
    if not len(falls):
        return np.empty((0, 6))
    level = float(np.median(i[np.abs(i) > ON_A]))
    q = charge_uah(t, i)
    rows = []
    for b in falls:
        nxt = rises[rises > b]
        end = (nxt[0] if len(nxt) else len(v)) - 1
        dt = t[b:end] - t[b - 1]
        sel = (dt >= K_WINDOW_S[0]) & (dt <= K_WINDOW_S[1])
        k = r0 = np.nan
        if sel.sum() > 3:
            slope, intercept = np.polyfit(np.sqrt(dt[sel]), v[b:end][sel], 1)
            k, r0 = slope / -level, (v[b - 1] - intercept) / level
        rows.append((q[b], v[b - 1], (v[b - 1] - v[b]) / level, k, v[end], r0))
    return np.array(rows)


def eta_at(t: np.ndarray, v: np.ndarray, i: np.ndarray, dt: float) -> tuple[float, float]:
    """Median over pulses of the current and of V(t_on + dt) - V(before the pulse).

    A pulse already on at the first reading has no baseline and is not counted;
    with no other pulse this raises (brief 14)."""
    rises, falls = edges(i)
    if not len(rises):
        raise ValueError("no pulse with a reading before it: no baseline")
    out = []
    for a in rises:
        after = falls[falls > a]
        b = after[0] if len(after) else len(t)
        k = min(int(np.searchsorted(t, t[a] + dt)), b - 1)
        out.append(v[k] - v[a - 1])
    return float(np.median(i[rises])), float(np.median(out))


def butler_volmer(i: Any, rs: float, b: float, i0: float) -> Any:
    """eta = I Rs + b asinh(I / 2 I0) (symmetric Butler-Volmer plus a series resistance)."""
    return i * rs + b * np.arcsinh(i / (2 * i0))


def table(path: Path, npz_current_mode: bool) -> None:
    t, v, i = load(path, npz_current_mode)
    rows = ici_pauses(t, v, i)
    q = charge_uah(t, i)[-1]
    print(
        f"{path.name}: {len(t)} records, {t[-1] / 3600:.2f} h, Q {q:+.1f} uAh, "
        f"{len(rows)} interruptions"
    )
    print("  n   Q uAh   V_load  R_first  R0 ohm  k ohm/s^0.5  V_pause_end")
    for n, (qq, vl, r, k, ve, r0) in enumerate(rows, 1):
        print(f"{n:3d} {qq:7.1f}  {vl:.4f}  {r:7.0f}  {r0:6.0f}  {k:10.0f}   {ve:.4f}")


def _pdf(out: Path) -> Any:
    import matplotlib

    matplotlib.use("pdf")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    plt.rcParams.update(
        {
            "lines.linewidth": 2,
            "lines.markersize": 4,
            "axes.grid": True,
            "grid.alpha": 0.25,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )
    return plt, PdfPages(out)


def _text_page(plt: Any, pdf: Any, summary: Path) -> None:
    fig = plt.figure(figsize=(8.27, 11.69))
    fig.text(0.07, 0.96, summary.read_text(), va="top", fontsize=8.6)
    pdf.savefig(fig)
    plt.close(fig)


def ici_report(
    d_file: Path, s_file: Path, summary: Path, out: Path, npz_current_mode: bool = False
) -> None:
    """Quasi-OCV, R and k against state of charge and voltage, both directions.

    The charge axis is the charge removed from the start of the desodiation run;
    the sodiation run is assumed to start where the desodiation run ended.
    """
    plt, pdf = _pdf(out)
    td, vd, id_ = load(d_file, npz_current_mode)
    ts, vs, is_ = load(s_file, npz_current_mode)
    d, s = ici_pauses(td, vd, id_), ici_pauses(ts, vs, is_)
    cd, cs = charge_uah(td, id_), charge_uah(ts, is_)
    qd = cd[-1]
    xd, xs = d[:, 0], qd + s[:, 0]
    print(f"Q desodiation {qd:.1f} uAh, sodiation {cs[-1]:.1f} uAh; {len(d)}/{len(s)} pauses")
    for x in (25, 50, 100, 150, 185):
        a, b = np.interp(x, xd, d[:, 4]), np.interp(x, xs[::-1], s[::-1, 4])
        print(f"x {x:3d} uAh: pause end d {a:.3f} s {b:.3f} V (hysteresis {1e3 * (a - b):.0f} mV)")
    _text_page(plt, pdf, summary)
    xl = f"Charge removed from the sodiated start (µAh); full range ≈ {qd:.0f} µAh"

    fig, ax = plt.subplots(figsize=(11.69, 8.27))
    ax.plot(cd, vd, color=BLUE, lw=0.5, alpha=0.5)
    ax.plot(qd + cs, vs, color=ORANGE, lw=0.5, alpha=0.5)
    ax.plot(xd, d[:, 4], "o-", color=BLUE, label="desodiation: voltage at the end of each pause")
    ax.plot(xs, s[:, 4], "o-", color=ORANGE, label="sodiation: voltage at the end of each pause")
    ax.set_xlabel(xl)
    ax.set_ylabel("Cell voltage (V)")
    ax.set_ylim(0, 1.6)
    ax.legend(loc="upper left", fontsize=9, title="Thin lines: full trace including the steps")
    fig.suptitle("Quasi-OCV against state of charge (ICI)")
    pdf.savefig(fig)
    plt.close(fig)

    for col, label, title in (
        (
            2,
            "R_first: switch-off step to the first sample, 6 ms (Ω)",
            "Resistance against state of charge (dashed: R0, the √t-fit intercept)",
        ),
        (
            3,
            "k = −dV/d√t / I, 0.1–1 s of the pause (Ω s$^{-1/2}$)",
            "Diffusion-like term k against state of charge",
        ),
    ):
        fig, ax = plt.subplots(1, 2, figsize=(11.69, 6))
        for x, rows, c, name in ((xd, d, BLUE, "desodiation"), (xs, s, ORANGE, "sodiation")):
            ax[0].plot(x, rows[:, col], "o-", color=c, label=name)
            ax[1].plot(rows[:, 4], rows[:, col], "o-", color=c, label=name)
            if col == 2:
                ax[0].plot(x, rows[:, 5], "--", color=c, lw=1, label=f"{name}: R0")
                ax[1].plot(rows[:, 4], rows[:, 5], "--", color=c, lw=1, label=f"{name}: R0")
        ax[0].set_xlabel(xl)
        ax[1].set_xlabel("Voltage at the end of the pause (V)")
        for a in ax:
            a.set_ylabel(label)
            a.legend(fontsize=9)
            a.set_ylim(bottom=0)
        fig.suptitle(title)
        fig.tight_layout()
        pdf.savefig(fig)
        plt.close(fig)
    pdf.close()
    print("written", out)


def series_report(directory: Path, summary: Path, out: Path) -> None:
    """The 12 Nov characterisation series (`explore.py --series b3`): six pages."""
    from scipy.optimize import curve_fit

    plt, pdf = _pdf(out)
    entries = [json.loads(line) for line in (directory / "series.jsonl").read_text().splitlines()]
    names = [e["row"]["measurement_id"] for e in entries]
    c1 = 260e-6  # 1C of the measured 0.26 mAh

    def run(name: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        return load(sorted(directory.glob(f"{name}_*.h5"))[-1])

    _text_page(plt, pdf, summary)

    fig, ax = plt.subplots(2, 1, figsize=(11.69, 8.27), sharex=True)
    t_start = None
    for e in entries:
        t, v, i = run(e["row"]["measurement_id"])
        stamp = e["file"].rsplit("_", 1)[1].removesuffix(".h5")
        t0 = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").timestamp()
        t_start = t_start or t0
        x = (t0 - t_start + t) / 60
        ax[0].plot(x, v, lw=0.5, color=BLUE)
        ax[1].plot(x, i * 1e3, lw=0.5, color=BLUE)
        if e["row"]["protocol"] != "rest" or e["row"].get("rest_s", 0) >= 300:
            ax[0].annotate(
                e["row"]["measurement_id"], (x[0], 1.5), rotation=90, fontsize=5, va="top"
            )
    ax[0].set_ylabel("Voltage (V)")
    ax[1].set_ylabel("Current (mA)")
    ax[1].set_xlabel("Minutes from the first run (file timestamps; gaps are host overhead)")
    fig.suptitle("Characterisation series: all runs")
    pdf.savefig(fig)
    plt.close(fig)

    fig, ax = plt.subplots(1, 2, figsize=(11.69, 5.5))
    for name, color in (("step-01d", BLUE), ("step-02d", ORANGE), ("step-02s", AQUA)):
        if name not in names:
            continue
        t, v, i = run(name)
        rises, falls = edges(i)
        a, b = rises[0], falls[0]
        ax[0].semilogx(
            t[a:b] - t[a - 1],
            (v[a:b] - v[a - 1]) / i[a],
            color=color,
            label=f"{name}: on, V_pre {v[a - 1]:.2f} V",
        )
        ax[1].semilogx(
            t[b:] - t[b - 1], 1e3 * (v[b:] - v[a - 1]), color=color, label=f"{name}: off"
        )
    ax[0].set_xlabel("Time since switch-on (s)")
    ax[0].set_ylabel("(V − V_pre) / I (Ω)")
    ax[1].set_xlabel("Time since switch-off (s)")
    ax[1].set_ylabel("V − V_pre (mV)")
    for a in ax:
        a.legend(fontsize=7)
    fig.suptitle("Steps at 1C: resistance against time")
    pdf.savefig(fig)
    plt.close(fig)

    groups = {
        "sodiated (0.23-0.30 V), desodiation": ["ref-01d"]
        + [n for n in names if n.startswith("amp-01d")],
        "after ICI (0.50-0.57 V), desodiation": ["ref-02d"]
        + [n for n in names if n.startswith("amp-02d")],
        "after ICI (0.50-0.57 V), sodiation": ["ref-02s"]
        + [n for n in names if n.startswith("amp-02s")],
    }
    fig, ax = plt.subplots(1, 2, figsize=(11.69, 5.5))
    for (label, rows), color in zip(groups.items(), (BLUE, ORANGE, AQUA), strict=True):
        pts = np.array([eta_at(*run(n), 0.5) for n in rows if n in names])
        if not len(pts):
            continue
        order = np.argsort(np.abs(pts[:, 0]))
        ii, ee = np.abs(pts[order, 0]), np.abs(pts[order, 1])
        ax[0].semilogx(ii / c1, ee, "o", color=color, label=label)
        try:
            p, _ = curve_fit(
                butler_volmer, ii, ee, p0=(50, 0.1, 1e-4), bounds=([0, 1e-3, 1e-7], [5e3, 2, 1])
            )
            xx = np.geomspace(ii.min(), ii.max(), 100)
            ax[0].semilogx(
                xx / c1,
                butler_volmer(xx, *p),
                "-",
                color=color,
                lw=0.8,
                label=f"fit: Rs {p[0]:.0f} Ω, b {p[1] * 1e3:.0f} mV, I0 {p[2] * 1e6:.0f} µA",
            )
            print(f"fit {label}: Rs {p[0]:.0f} ohm, b {p[1] * 1e3:.0f} mV, I0 {p[2] * 1e6:.1f} uA")
        except (RuntimeError, ValueError) as exc:
            print(label, "fit failed:", exc)
        ax[1].semilogx(ii / c1, ee / ii, "o-", color=color, label=label)
    ax[0].set_ylabel("|V − V_pre| at 0.5 s (V)")
    ax[1].set_ylabel("|V − V_pre| / |I| at 0.5 s (Ω)")
    for a in ax:
        a.set_xlabel("Current (C-rate, 1C = 260 µA)")
        a.legend(fontsize=6)
    fig.suptitle("Overpotential at 0.5 s against current (median of the pulses)")
    pdf.savefig(fig)
    plt.close(fig)

    fig, ax = plt.subplots(1, 3, figsize=(11.69, 5))
    for name, color in (("ici-d", ORANGE), ("ici-s", AQUA)):
        if name not in names:
            continue
        rows = ici_pauses(*run(name))
        ax[0].plot(rows[:, 4], rows[:, 2], "o-", color=color, label=name)
        ax[1].plot(rows[:, 4], rows[:, 3], "o-", color=color, label=name)
        ax[2].plot(rows[:, 0], rows[:, 4], "o-", color=color, label=f"{name}: pause end")
        ax[2].plot(rows[:, 0], rows[:, 1], "x--", color=color, label=f"{name}: under load")
    ax[0].set_ylabel("R_first, switch-off step to the first sample (Ω)")
    ax[1].set_ylabel("k, 0.1–1 s (Ω s$^{-1/2}$)")
    ax[2].set_ylabel("Voltage (V)")
    ax[0].set_xlabel("Voltage at the end of the pause (V)")
    ax[1].set_xlabel("Voltage at the end of the pause (V)")
    ax[2].set_xlabel("Charge within the run (µAh, + desodiation)")
    for a in ax:
        a.legend(fontsize=7)
    fig.suptitle("ICI at 1C: 60 s current, 10 s pauses")
    fig.tight_layout()
    pdf.savefig(fig)
    plt.close(fig)

    rests = [r for r in (1, 3, 10, 30, 100, 300) if f"rest-{r}s" in names]
    if rests:
        fig, ax = plt.subplots(1, 2, figsize=(11.69, 5))
        for dt, a in ((0.012, ax[0]), (0.5, ax[1])):
            for r in rests:
                t, v, i = run(f"rest-{r}s")
                rises, _ = edges(i)
                vals = [(v[np.searchsorted(t, t[k] + dt)] - v[k - 1]) / i[k] for k in rises]
                a.plot(range(1, len(vals) + 1), vals, "o-", label=f"rest {r} s")
                print(f"rest {r:3d} s, R at {dt} s:", [round(float(x)) for x in vals])
            a.set_xlabel("Pulse number (1 s at +1C; 60 s before pulse 1)")
            a.set_ylabel(f"(V − V_pre) / I at {dt * 1e3:.0f} ms (Ω)")
            a.legend(fontsize=7)
        fig.suptitle("How long the conditioned state lasts: trains with different rests")
        pdf.savefig(fig)
        plt.close(fig)
    pdf.close()
    print("written", out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    t = sub.add_parser("table", help="print the ICI table of each file")
    t.add_argument("files", type=Path, nargs="+")
    t.add_argument("--npz-current-mode", action="store_true", help=NPZ_HELP)
    s = sub.add_parser("series-report", help="PDF of a characterisation series directory")
    s.add_argument("directory", type=Path)
    s.add_argument("summary", type=Path, help="text for the first page")
    s.add_argument("out", type=Path)
    r = sub.add_parser("ici-report", help="PDF of a desodiation and a sodiation ICI run")
    r.add_argument("d_file", type=Path)
    r.add_argument("s_file", type=Path)
    r.add_argument("summary", type=Path, help="text for the first page")
    r.add_argument("out", type=Path)
    r.add_argument("--npz-current-mode", action="store_true", help=NPZ_HELP)
    args = parser.parse_args(argv)
    if args.command == "table":
        for path in args.files:
            table(path.expanduser(), args.npz_current_mode)
    elif args.command == "series-report":
        series_report(args.directory.expanduser(), args.summary, args.out)
    else:
        ici_report(
            args.d_file.expanduser(),
            args.s_file.expanduser(),
            args.summary,
            args.out,
            args.npz_current_mode,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
