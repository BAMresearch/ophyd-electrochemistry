"""B2: watch the RE Manager and the run files, then summarize a day into evidence.

Read-only: never contacts the instrument or the RE Manager. ``watch`` samples
CPU and memory of every process under ``start-re-manager`` and times what the
viewer does (snapshot read + figure) on the newest run file. ``evidence``
writes one JSON file per load from the run files and the watch CSV.

    uv run --locked --extra experiment python bench/b2_monitor.py watch ~/k2460-data/b2/cell \\
        --csv ~/k2460-data/b2/cell/monitor.csv
    uv run --locked --extra experiment python bench/b2_monitor.py evidence --load cell \\
        ~/k2460-data/b2/cell --csv ~/k2460-data/b2/cell/monitor.csv
"""

import argparse
import csv
import datetime
import json
import subprocess
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from ophyd_electrochemistry.sequences.run_file import read_run_file
from ophyd_electrochemistry.sequences.viewer import _run_figure, latest_run, run_index

EVIDENCE = Path("docs/evidence")
REST_A = 1e-6  # |I| below this counts as a zero-current (rest) sample
FIELDS = (
    *("utc", "pid", "ppid", "cpu_pct", "rss_mb", "command"),
    *("file", "records", "read_s", "figure_s"),
)


def processes() -> list[dict[str, Any]]:
    """Every process under a start-re-manager root (manager, watchdog, worker)."""
    table = subprocess.run(
        ["ps", "-A", "-o", "pid=,ppid=,pcpu=,rss=,command="], capture_output=True, text=True
    ).stdout
    rows: dict[int, dict[str, Any]] = {}
    roots: list[int] = []
    for line in table.splitlines():
        pid, ppid, cpu, rss, command = line.split(None, 4)
        rows[int(pid)] = dict(
            pid=int(pid),
            ppid=int(ppid),
            cpu_pct=float(cpu),
            rss_mb=round(int(rss) / 1024, 1),
            command=command[:80],
        )
        # The program or the script run by python, not a shell mentioning it.
        if any(word.endswith("start-re-manager") for word in command.split()[:2]):
            roots.append(int(pid))
    children: dict[int, list[int]] = defaultdict(list)
    for row in rows.values():
        children[row["ppid"]].append(row["pid"])
    found, todo = [], roots
    while todo:
        node = todo.pop()
        if node in rows and rows[node] not in found:
            found.append(rows[node])
            todo.extend(children[node])
    return found


def viewer_cost(data_dir: Path) -> dict[str, Any]:
    """Time one viewer refresh (snapshot read, figure) on the newest run file."""
    path = latest_run(data_dir)
    if path is None:
        return dict(file="", records=0, read_s=0.0, figure_s=0.0)
    start = time.perf_counter()
    data = read_run_file(path)
    read_s = time.perf_counter() - start
    _run_figure(data, "time", 20_000).to_plotly_json()
    return dict(
        file=path.name,
        records=len(data["readout"]["t_s"]),
        read_s=round(read_s, 4),
        figure_s=round(time.perf_counter() - start - read_s, 4),
    )


def watch(data_dir: Path, csv_path: Path, period_s: float, count: int | None = None) -> None:
    new = not csv_path.exists()
    with csv_path.open("a", newline="") as stream:
        writer = csv.DictWriter(stream, FIELDS)
        if new:
            writer.writeheader()
        n = 0
        try:
            while count is None or n < count:
                utc = datetime.datetime.now(datetime.UTC).isoformat(timespec="milliseconds")
                try:
                    cost = viewer_cost(data_dir)
                except Exception as exc:  # a file being created; next sample
                    cost = dict(file=f"error: {exc}"[:80], records=0, read_s=0.0, figure_s=0.0)
                procs = processes() or [dict(pid=0, ppid=0, cpu_pct=0.0, rss_mb=0.0, command="")]
                for proc in procs:
                    writer.writerow(dict(utc=utc, **proc, **cost))
                stream.flush()
                biggest = max(procs, key=lambda p: p["cpu_pct"])
                print(
                    f"{utc} {len(procs)} proc, max {biggest['cpu_pct']}% CPU "
                    f"(pid {biggest['pid']}), {cost['file']} {cost['records']} rec, "
                    f"view {cost['read_s'] + cost['figure_s']:.2f} s",
                    flush=True,
                )
                n += 1
                if count is None or n < count:
                    time.sleep(period_s)
        except KeyboardInterrupt:
            pass


def _stats(values: np.ndarray) -> dict[str, float] | None:
    if not len(values):
        return None
    return {
        "median": float(np.median(values)),
        "p99": float(np.percentile(values, 99)),
        "max": float(np.max(values)),
    }


def _clusters(spacing: np.ndarray, ratio: float = 1.25) -> list[np.ndarray]:
    """Spacings grouped by nominal interval: a new group starts 25 % above the last."""
    groups: list[list[float]] = []
    for x in np.sort(spacing):
        if groups and x <= groups[-1][0] * ratio:
            groups[-1].append(float(x))
        else:
            groups.append([float(x)])
    return [np.asarray(g) for g in groups]


def run_checks(path: Path) -> dict[str, Any]:
    """Numbers an engineer reads first: spacing, rest voltages, apparent resistance."""
    data = read_run_file(path)
    r = data["readout"]
    t, v, i, step = (np.asarray(r[k]) for k in ("t_s", "voltage_v", "current_a", "step_index"))
    same_step = np.diff(step) == 0 if len(t) > 1 else np.zeros(0, bool)
    spacing = np.diff(t)[same_step]
    rest = np.abs(i) < REST_A
    driven = ~rest
    result: dict[str, Any] = {
        "measurement_id": data["attrs"]["measurement_id"],
        "outcome": data["run"]["outcome"],
        "records": int(len(t)),
        "duration_s": float(t[-1]) if len(t) else 0.0,
        "spacing_s": {
            f"{np.median(group):.3g}s": {"n": len(group), **(_stats(group) or {})}
            for group in _clusters(spacing)
        },
        "spacing_max_s": float(spacing.max()) if len(spacing) else None,
        "rest_v_first_last": [float(v[rest][0]), float(v[rest][-1])] if rest.any() else None,
        "v_min_max": [float(v.min()), float(v.max())] if len(v) else None,
        "i_min_max": [float(i.min()), float(i.max())] if len(i) else None,
        "events": [
            f"{t_s:.3f} {kind} {detail}"
            for t_s, kind, detail in zip(
                data["events"]["t_s"], data["events"]["kind"], data["events"]["detail"], strict=True
            )
            if kind != "step"
        ][:50],
    }
    if driven.any() and np.ptp(i[driven]) > 0:
        # V = V0 + R*I over all samples: positive R = polarity as wired (HC on HI).
        slope, _ = np.polyfit(i, v, 1)
        result["apparent_r_ohm"] = float(slope)
    if driven.any():
        result["median_v_over_i_ohm"] = float(np.median(v[driven] / i[driven]))
    return result


def monitor_summary(csv_path: Path) -> dict[str, Any]:
    with csv_path.open() as stream:
        rows = list(csv.DictReader(stream))
    per_pid: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        if row["pid"] != "0":
            per_pid[row["pid"]].append(row)
    views = {(row["utc"], row["file"]): row for row in rows if row["file"]}
    view_s = [float(row["read_s"]) + float(row["figure_s"]) for row in views.values()]
    return {
        "samples": len({row["utc"] for row in rows}),
        "first_utc": rows[0]["utc"] if rows else None,
        "last_utc": rows[-1]["utc"] if rows else None,
        "processes": {
            pid: {
                "command": samples[0]["command"],
                "cpu_pct_median": float(np.median([float(s["cpu_pct"]) for s in samples])),
                "cpu_pct_max": max(float(s["cpu_pct"]) for s in samples),
                "rss_mb_first_last_max": [
                    float(samples[0]["rss_mb"]),
                    float(samples[-1]["rss_mb"]),
                    max(float(s["rss_mb"]) for s in samples),
                ],
            }
            for pid, samples in per_pid.items()
        },
        "viewer_refresh_s_max": max(view_s, default=None),
        "viewer_refresh_at_most_records": max(
            (int(row["records"]) for row in views.values()), default=0
        ),
    }


def evidence(load: str, data_dirs: list[Path], csv_path: Path | None, tag: str | None) -> Path:
    date = datetime.date.today().isoformat()
    name = f"k2460-{date}-b2-{load}{f'-{tag}' if tag else ''}.json"
    runs = []
    for data_dir in data_dirs:
        index = run_index(data_dir)
        for row in index.to_dict("records"):
            entry = {k: (str(v) if k == "file" else v) for k, v in row.items()}
            if row["outcome"] != "unreadable":
                try:
                    entry["checks"] = run_checks(Path(row["file"]))
                except Exception as exc:
                    entry["checks"] = {"error": str(exc)}
            runs.append(entry)
    result = {
        "test_matrix": "bench/plan-b2.md",
        "load": load,
        "written_utc": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "data_dirs": [str(d) for d in data_dirs],
        "runs": runs,
        "monitor": monitor_summary(csv_path) if csv_path and csv_path.exists() else None,
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    out = EVIDENCE / name
    out.write_text(json.dumps(result, indent=2, default=float) + "\n")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    w = sub.add_parser("watch", help="sample processes and viewer cost until Ctrl-C")
    w.add_argument("data_dir", type=Path)
    w.add_argument("--csv", type=Path, required=True)
    w.add_argument("--period-s", type=float, default=30.0)
    w.add_argument("--count", type=int, help="stop after this many samples")
    e = sub.add_parser("evidence", help="write docs/evidence/k2460-<date>-b2-<load>.json")
    e.add_argument("data_dirs", type=Path, nargs="+")
    e.add_argument("--load", choices=("resistor", "cell", "sim"), required=True)
    e.add_argument("--csv", type=Path, help="the watch CSV")
    e.add_argument("--tag")
    args = parser.parse_args(argv)
    if args.command == "watch":
        watch(args.data_dir.expanduser(), args.csv.expanduser(), args.period_s, args.count)
    else:
        dirs = [d.expanduser() for d in args.data_dirs]
        print(evidence(args.load, dirs, args.csv and args.csv.expanduser(), args.tag))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
