"""Read-only run-file views; requires the ``experiment`` extra.

Snapshots use the SWMR reader, including while a QueueServer worker writes.
Only display data are decimated; charge is integrated from every saved record.
"""

import time
from collections.abc import Callable, Iterable, Mapping
from os import PathLike
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
import plotly.graph_objects as go  # type: ignore[import-untyped]
from numpy.typing import NDArray

from ..validation import integer, positive
from .callbacks import sequence_figure
from .run_file import read_run_file

_Path = str | PathLike[str]
_Axis = Literal["time", "charge"]
_COLUMNS = (
    "file",
    "measurement_id",
    "sample_id",
    "protocol",
    "start_utc",
    "duration_s",
    "records",
    "outcome",
    "cutoffs",
    "stops",
    "aborts",
    "errors",
    "detail",
)


def _paths(data_dir: _Path) -> list[Path]:
    def modified(path: Path) -> tuple[int, str]:
        try:
            return path.stat().st_mtime_ns, path.name
        except OSError:  # A file removed during discovery will be an unreadable row.
            return -1, path.name

    paths = (path for path in Path(data_dir).glob("*.h5") if path.is_file())
    return sorted(paths, key=modified, reverse=True)


def latest_run(data_dir: _Path) -> Path | None:
    """Newest directly contained HDF5 file by modification time, or None."""
    return next(iter(_paths(data_dir)), None)


def run_index(data_dir: _Path) -> pd.DataFrame:
    """List run-file snapshots, newest modified first; isolate unreadable files."""
    rows = []
    for path in _paths(data_dir):
        row: dict[str, Any] = dict.fromkeys(_COLUMNS, "")
        row.update(file=path, duration_s=0.0, records=0, cutoffs=0, stops=0, aborts=0, errors=0)
        try:
            data = read_run_file(path)
            run, readout = data["run"], data["readout"]
            kinds = data["events"]["kind"]
            row.update(
                measurement_id=data["attrs"]["measurement_id"],
                sample_id=run["sample_id"],
                protocol=data["run_sheet_row"].get("protocol", ""),
                start_utc=run.get("time_origin_utc", run["started_utc"]),
                duration_s=float(readout["t_s"][-1]) if len(readout["t_s"]) else 0.0,
                records=len(readout["t_s"]),
                outcome=run["outcome"],
                detail=run["outcome_detail"],
            )
            for column, kind in (
                ("cutoffs", "cutoff"),
                ("stops", "stop"),
                ("aborts", "abort"),
                ("errors", "error"),
            ):
                row[column] = int(np.count_nonzero(kinds == kind))
        except Exception as exc:
            row.update(outcome="unreadable", detail=str(exc))
        rows.append(row)
    return pd.DataFrame(rows, columns=_COLUMNS)


def charge_mah(readout: Mapping[str, NDArray[Any]]) -> NDArray[np.float64]:
    """Signed trapezoidal integral of measured current, starting at zero (mAh)."""
    times = np.asarray(readout["t_s"], dtype=float)
    current = np.asarray(readout["current_a"], dtype=float)
    charge = np.zeros(len(times), dtype=float)
    charge[1:] = np.cumsum(np.diff(times) * (current[:-1] + current[1:]) / 2) / 3.6
    return charge


def decimate(x: NDArray[Any], y: NDArray[Any], max_points: int) -> NDArray[np.intp]:
    """Sorted unique endpoint and equal-count-bin extrema indices.

    Reserve two slots for endpoints, then use (max_points - 2) // 2 bins.
    This reconciles retaining both extrema per bin with the hard point budget.
    A budget of two keeps just endpoints; three also keeps the largest departure
    from the endpoint midpoint. Nonfinite values remain visible as plot gaps.
    """
    budget = integer(max_points, "max_points", minimum=2)
    if x.ndim != 1 or y.ndim != 1 or len(x) != len(y):
        raise ValueError("x and y must be equal-length 1-D arrays")
    count = len(x)
    if count <= budget:
        return np.arange(count, dtype=np.intp)
    indices = [0, count - 1]
    if budget == 3:
        deviation = np.abs(y[1:-1] - (y[0] / 2 + y[-1] / 2))
        indices.append(1 + int(np.argmax(deviation)))
    elif budget > 3:
        edges = np.linspace(0, count, (budget - 2) // 2 + 1, dtype=np.intp)
        for start, end in zip(edges[:-1], edges[1:], strict=True):
            values = y[start:end]
            indices.extend((int(start + np.argmin(values)), int(start + np.argmax(values))))
    return np.unique(np.asarray(indices, dtype=np.intp))


def _abscissa(readout: Mapping[str, NDArray[Any]], x: _Axis) -> NDArray[Any]:
    if x == "time":
        return np.asarray(readout["t_s"])
    if x == "charge":
        return charge_mah(readout)
    raise ValueError("x must be 'time' or 'charge'")


def _axis_title(x: _Axis) -> str:
    return "Time since start (s)" if x == "time" else "Charge (mAh)"


def _run_figure(data: dict[str, Any], x: _Axis, max_points: int) -> go.Figure:
    readout, events = data["readout"], data["events"]
    axis = _abscissa(readout, x)
    event_x = events["t_s"]
    if x == "charge":
        # Charge need not be monotonic; interpolate it using the saved time axis.
        event_x = np.interp(event_x, readout["t_s"], axis) if len(axis) else []
    markers = (
        list(zip(event_x, events["kind"], events["detail"], strict=True))
        if (len(axis) or x == "time")
        else []
    )
    title = f"{data['attrs']['measurement_id']} — {data['run']['outcome']} — {len(axis)} records"
    # Reuse the layout without ever passing full data through its stride sampler.
    fig = sequence_figure({key: [] for key in ("t_s", "voltage_v", "current_a")}, markers, title)
    for trace, key in zip(fig.data, ("voltage_v", "current_a"), strict=True):
        indices = decimate(axis, readout[key], max_points)
        trace.x, trace.y = axis[indices], readout[key][indices]
    fig.update_xaxes(title_text=_axis_title(x), row=2, col=1)
    fig.update_layout(uirevision="run-viewer")
    return fig


def run_figure(path: _Path, *, x: _Axis = "time", max_points: int = 20_000) -> go.Figure:
    """Voltage above current, with per-trace extrema-preserving display sampling.

    Pass max_points >= the saved record count for a full-resolution view.
    """
    return _run_figure(read_run_file(path), x, max_points)


def overlay_figure(
    paths: Iterable[_Path], *, x: _Axis = "charge", max_points: int = 20_000
) -> go.Figure:
    """Compare voltage traces; each run starts at zero time or signed charge."""
    integer(max_points, "max_points", minimum=2)
    if x not in ("time", "charge"):
        raise ValueError("x must be 'time' or 'charge'")
    fig = go.Figure()
    for path in paths:
        data = read_run_file(path)
        axis = _abscissa(data["readout"], x)
        if len(axis):
            axis = axis - axis[0]
        voltage = data["readout"]["voltage_v"]
        indices = decimate(axis, voltage, max_points)
        label = data["attrs"]["measurement_id"]
        if data["run"]["sample_id"]:
            label += f" ({data['run']['sample_id']})"
        fig.add_trace(go.Scatter(x=axis[indices], y=voltage[indices], mode="lines", name=label))
    fig.update_layout(title="Run comparison", xaxis_title=_axis_title(x), yaxis_title="Voltage (V)")
    return fig


def follow(
    path: _Path,
    *,
    period_s: float = 2.0,
    max_points: int = 20_000,
    timeout_s: float | None = None,
    display: Callable[..., Any] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Refresh one notebook output until a terminal outcome, timeout or stop button.

    A timeout or KeyboardInterrupt returns the last observed file outcome.
    An incomplete file can be live or left behind by a crashed writer.
    """
    period_s = positive(period_s, "period_s")
    integer(max_points, "max_points", minimum=2)
    if timeout_s is not None:
        timeout_s = positive(timeout_s, "timeout_s", zero=True)
    if display is None:
        from IPython.display import display as ipython_display

        display = ipython_display
    render: Callable[..., Any] = display

    deadline = None if timeout_s is None else time.monotonic() + timeout_s
    handle = None
    outcome = "incomplete"
    try:
        while True:
            data = read_run_file(path)
            outcome = str(data["run"]["outcome"])
            fig = _run_figure(data, "time", max_points)
            bundle = fig._repr_mimebundle_()
            if handle is None:
                handle = render(bundle, raw=True, display_id=True)
            else:
                handle.update(bundle, raw=True)
            if outcome != "incomplete":
                return outcome
            remaining = period_s if deadline is None else deadline - time.monotonic()
            if remaining <= 0:
                return outcome
            sleep(min(period_s, remaining))
    except KeyboardInterrupt:
        return outcome
