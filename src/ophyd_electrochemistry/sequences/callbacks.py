"""Optional HDF5 and Plotly subscribers for ``run_sequence`` documents."""

# mypy: disable-error-code="no-untyped-call,misc"

import json
import math
import re
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from os import PathLike
from pathlib import Path
from typing import Any

import plotly.graph_objects as go  # type: ignore[import-untyped]
from bluesky.callbacks.core import CallbackBase
from event_model import pack_event_page
from event_model.documents import Event, EventDescriptor, EventPage, RunStart, RunStop
from plotly.subplots import make_subplots  # type: ignore[import-untyped]

from ..validation import integer, positive
from .backend import READOUT_FIELDS
from .models import limits_from_dict, sequence_from_dict
from .run_file import RunFile, host_provenance, read_run_file

# RunStart keys stored in their own place in the run file; every other key
# (custom metadata, RunEngine md) goes to /entry/run/extra.
_START_KEYS = {
    "measurement_id",
    "sample_id",
    "notes",
    "start_mode",
    "sequence",
    "safety_limits",
    "run_sheet_row",
    "equipment",
    "expected_records",
    "nominal_duration_s",
    "plan_name",
    "uid",
    "scan_id",
    "time",
    "versions",
    "run_sheet",
    "sheet_sha256",
}


def _jsonable(value: Any) -> Any:
    """JSON-safe copy of RunStart metadata; anything else is kept as its repr."""
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError, OverflowError):
        if isinstance(value, Mapping):
            return {str(k): _jsonable(v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [_jsonable(v) for v in value]
        return repr(value)


def _provenance(doc: Mapping[str, Any]) -> dict[str, Any]:
    # The RunStart "versions" (bluesky, ophyd) are in host_provenance's software.
    return {
        "bluesky_uid": doc.get("uid"),
        "scan_id": doc.get("scan_id"),
        "run_start_time": doc.get("time"),
        "plan_name": doc.get("plan_name"),
        "run_sheet_path": doc.get("run_sheet"),
        "run_sheet_sha256": doc.get("sheet_sha256"),
        **host_provenance(),
    }


class _SequenceCallback(CallbackBase):
    """Route only streams belonging to the current sequence run."""

    def __init__(self) -> None:
        super().__init__()
        self._uid: str | None = None
        self._streams: dict[str, str] = {}

    def _start(self, doc: Mapping[str, Any]) -> bool:
        if doc.get("plan_name") != "run_sequence":
            return False
        self._uid = doc["uid"]
        self._streams = {}
        return True

    def descriptor(self, doc: EventDescriptor) -> None:
        if self._uid is not None and doc["run_start"] == self._uid:
            self._streams[doc["uid"]] = doc["name"]

    def event(self, doc: Event) -> Event:
        self.event_page(pack_event_page(doc))
        return doc

    def event_page(self, doc: EventPage) -> EventPage:
        return doc


class RunFileWriter(_SequenceCallback):
    """Incrementally persist all readout pages; errors propagate to the RunEngine.

    ``session`` is what each file stores about the environment
    (``queueserver.session_metadata``). The RunStart uid, scan_id and time, the
    run sheet (``run_sheet``/``sheet_sha256`` metadata), host and software versions
    go to ``/entry/provenance``; other RunStart metadata to ``/entry/run/extra``.
    """

    def __init__(
        self, directory: str | PathLike[str], *, session: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__()
        self.directory = Path(directory)
        self.session = dict(session or {})
        self.path: Path | None = None
        self._file: RunFile | None = None
        self._origin_set = False
        self._stopped = False
        self._stop_detail = ""

    def _close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def start(self, doc: RunStart) -> None:
        if doc.get("plan_name") != "run_sequence":
            return
        self._close()
        self._start(doc)
        metadata: dict[str, Any] = dict(doc)
        self.path = None
        self._origin_set = self._stopped = False
        self._stop_detail = ""
        try:
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", metadata["measurement_id"])
            stamp = datetime.fromtimestamp(metadata["time"], UTC).strftime("%Y%m%dT%H%M%SZ")
            self.directory.mkdir(parents=True, exist_ok=True)
            self.path = self.directory / f"{safe}_{stamp}.h5"
            self._file = RunFile.create(
                self.path,
                measurement_id=metadata["measurement_id"],
                equipment=metadata["equipment"],
                sequence=sequence_from_dict(metadata["sequence"]),
                limits=limits_from_dict(metadata["safety_limits"]),
                start_mode=metadata["start_mode"],
                sample_id=metadata.get("sample_id"),
                notes=metadata.get("notes"),
                run_sheet_row=metadata["run_sheet_row"],
                extra={k: _jsonable(v) for k, v in metadata.items() if k not in _START_KEYS},
                session=self.session,
                provenance=_provenance(metadata),
            )
        except BaseException:
            self._close()
            raise

    def event_page(self, doc: EventPage) -> EventPage:
        if self._file is None:
            return doc
        try:
            stream = self._streams.get(doc["descriptor"])
            data = doc["data"]
            if stream == "readout":
                if len(doc["time"]) and not self._origin_set:
                    # Bluesky 1.15.1 recomposes collect events without their envelope
                    # time. Field timestamps retain the acquisition UTC anchor.
                    timestamp = doc["timestamps"]["t_s"][0]
                    self._file.set_time_origin(float(timestamp) - float(data["t_s"][0]))
                    self._origin_set = True
                self._file.append_readout({key: data[key] for key in READOUT_FIELDS})
            elif stream == "sequence_events":
                for values in zip(
                    *(
                        data[key]
                        for key in (
                            "event_t_s",
                            "event_step_index",
                            "event_repeat_index",
                            "event_kind",
                            "event_detail",
                        )
                    ),
                    strict=True,
                ):
                    self._file.append_event(*values)
                    if values[3] == "stop":
                        self._stopped, self._stop_detail = True, values[4]
        except BaseException:
            self._close()
            raise
        return doc

    def stop(self, doc: RunStop) -> None:
        if self._file is None or doc["run_start"] != self._uid:
            return
        try:
            status = doc["exit_status"]
            outcome = (
                "aborted"
                if status == "abort"
                else "error"
                if status == "fail"
                else "stopped_cutoff"
                if self._stopped
                else "completed"
            )
            detail = doc.get("reason", "") if status in ("abort", "fail") else self._stop_detail
            self._file.finalize(outcome, detail)
        finally:
            self._close()


def sequence_figure(
    readout: dict[str, Any],
    events: list[tuple[float, str, str]],
    title: str,
    max_points: int | None = None,
) -> Any:
    """Shared voltage/current layout for previews, live data and saved runs."""
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.08)
    count = len(readout["t_s"])
    stride = 1 if max_points is None else max(1, math.ceil(count / max_points))
    for row, key, label in ((1, "voltage_v", "Voltage (V)"), (2, "current_a", "Current (A)")):
        fig.add_trace(
            go.Scatter(
                x=readout["t_s"][::stride],
                y=readout[key][::stride],
                mode="lines",
                name=label,
            ),
            row=row,
            col=1,
        )
        fig.update_yaxes(title_text=label, row=row, col=1)
    for t_s, kind, detail in events:
        if kind in ("cutoff", "stop", "abort"):
            fig.add_vline(x=t_s, annotation_text=f"{kind}: {detail}", exclude_empty_subplots=False)
    fig.update_xaxes(title_text="Time since start (s)", row=2, col=1)
    fig.update_layout(title=title, showlegend=False)
    return fig


class LiveSequencePlot(_SequenceCallback):
    """Throttled display with full accumulation and display-only stride decimation."""

    def __init__(
        self,
        *,
        title: str | None = None,
        min_redraw_s: float = 2.0,
        max_points: int = 20000,
        render: Callable[[Any], None] | None = None,
    ) -> None:
        super().__init__()
        self._title = title
        self._minimum = positive(min_redraw_s, "min_redraw_s", zero=True)
        self._max_points = integer(max_points, "max_points")
        self._render = render or self._display
        self._handle: Any = None
        self._last_redraw = -math.inf
        self._readout: dict[str, list[Any]] = {key: [] for key in READOUT_FIELDS}
        self._events: list[tuple[float, str, str]] = []
        self._measurement_id = ""
        self._outcome = "running"

    def _display(self, fig: Any) -> None:
        from IPython.display import display

        # Plotly figures implement _ipython_display_, which ignores display_id:
        # every update would append a new figure. A raw MIME bundle keeps one
        # output that is replaced in place.
        bundle = fig._repr_mimebundle_()
        if self._handle is None:
            self._handle = display(bundle, raw=True, display_id=True)
        else:
            self._handle.update(bundle, raw=True)

    def start(self, doc: RunStart) -> None:
        if not self._start(doc):
            return
        metadata: dict[str, Any] = dict(doc)
        self._measurement_id = metadata["measurement_id"]
        self._outcome = "running"
        self._readout = {key: [] for key in READOUT_FIELDS}
        self._events = []
        self._last_redraw = -math.inf
        self._handle = None

    def _redraw(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_redraw < self._minimum:
            return
        title = f"{self._measurement_id} — {self._outcome}"
        if self._title:
            title = f"{self._title}: {title}"
        self._render(sequence_figure(self._readout, self._events, title, self._max_points))
        self._last_redraw = now

    def event_page(self, doc: EventPage) -> EventPage:
        stream = self._streams.get(doc["descriptor"])
        data = doc["data"]
        if stream == "readout":
            for key in READOUT_FIELDS:
                self._readout[key].extend(data[key])
        elif stream == "sequence_events":
            self._events.extend(
                zip(data["event_t_s"], data["event_kind"], data["event_detail"], strict=True)
            )
        else:
            return doc
        self._redraw()
        return doc

    def stop(self, doc: RunStop) -> None:
        if doc["run_start"] != self._uid:
            return
        status = doc["exit_status"]
        self._outcome = (
            "aborted"
            if status == "abort"
            else "error"
            if status == "fail"
            else "stopped_cutoff"
            if any(e[1] == "stop" for e in self._events)
            else "completed"
        )
        self._redraw(force=True)


def plot_run_file(path: str | PathLike[str]) -> Any:
    """Full-resolution static view of the same V/I layout used by the live plot."""
    data = read_run_file(path)
    events = list(
        zip(data["events"]["t_s"], data["events"]["kind"], data["events"]["detail"], strict=True)
    )
    return sequence_figure(
        data["readout"], events, f"{data['attrs']['measurement_id']} — {data['run']['outcome']}"
    )
