"""Incremental per-run HDF5 files (install the ``experiment`` extra).

Every append and finalization flushes the file. Files are written in HDF5 SWMR
mode (single writer, multiple readers), so ``read_run_file(path)`` returns a
fresh snapshot from any process while a run is in progress, e.g. a notebook
plotting a run written by a QueueServer worker. Keep run files on a local
disk while they are written: SWMR is not reliable on network file systems.

The file format is pinned to HDF5 1.10 (the minimum for SWMR), so HDF5 1.10+
tools read it. SWMR's documented guarantees cover appends to the numeric
readout datasets; the UTF-8 event datasets and the outcome attributes are also
updated during the run, which a cross-process stress test with HDF5 2.0 read
back without errors.

A killed writer (e.g. a crashed Jupyter kernel) leaves the file flagged as
open for writing. ``read_run_file`` still reads everything flushed before the
crash, with outcome ``incomplete``; other HDF5 tools refuse the file until it
is copied with ``repair_run_file`` (or cleared with HDF5's ``h5clear``).
Closing, including context-manager exception cleanup, preserves received data.
Flush is not an fsync or a power-loss guarantee.
"""

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from os import PathLike
from types import TracebackType
from typing import Any

import h5py  # type: ignore[import-untyped]
import numpy as np
from numpy.typing import ArrayLike, NDArray

from .. import __version__
from ..exceptions import ValidationError
from ..validation import number
from .models import SafetyLimits, Sequence, to_dict

SCHEMA = "ophyd-electrochemistry.run-file.v1"
READOUT_FIELDS: tuple[str, ...] = (
    "t_s",
    "voltage_v",
    "current_a",
    "step_index",
    "repeat_index",
    "source_status",
    "measure_status",
)
_UNITS = dict(zip(READOUT_FIELDS, ("s", "V", "A", "", "", "", ""), strict=True))
_EVENT_FIELDS = ("t_s", "step_index", "repeat_index", "kind", "detail")
_EVENT_KINDS = {"step_start", "cutoff", "stop", "abort", "error"}
_OUTCOMES = {"completed", "stopped_cutoff", "aborted", "error"}
_Path = str | PathLike[str]
# HDF5 1.10 file format: the minimum for SWMR, readable by HDF5 1.10+ tools.
_LIBVER = ("v110", "v110")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or "\x00" in value:
        raise ValidationError(f"{name} must be a string without NUL characters")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise ValidationError(f"{name} must be valid UTF-8") from exc
    return value


def _json(value: Mapping[str, object], name: str) -> str:
    try:
        return json.dumps(dict(value), sort_keys=True, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValidationError(f"{name} must be JSON-safe: {exc}") from exc


def _equipment(settings: Mapping[str, object]) -> dict[str, object]:
    result = dict(settings)
    for key, value in result.items():
        _text(key, "equipment key")
        if not key or not isinstance(value, (str, int, float, bool)):
            raise ValidationError(f"equipment.{key} must be a flat str/int/float/bool setting")
        if isinstance(value, str):
            _text(value, f"equipment.{key}")
        elif isinstance(value, int) and not -(2**63) <= value < 2**64:
            raise ValidationError(f"equipment.{key} exceeds HDF5 integer range")
    _json(result, "equipment")
    return result


def _array(value: ArrayLike, name: str) -> NDArray[Any]:
    """Validate before resizing, preventing partial writes on bad input."""
    try:
        array = np.asarray(value)
        if array.ndim != 1 or array.dtype.kind not in "iuf":
            raise ValidationError(f"{name} must be a one-dimensional real numeric array")
        dtype = "f8" if name in ("t_s", "voltage_v", "current_a") else "i4"
        if dtype == "i4" and (
            not np.all(np.isfinite(array))
            or np.any(array < -(2**31))
            or np.any(array >= 2**31)
            or np.any(array != np.floor(array))
        ):
            raise ValidationError(f"{name} must contain int32 values without truncation")
        return array.astype(dtype)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValidationError(f"invalid {name} array: {exc}") from exc


class RunFile:
    """One writer per run; closing never implies successful completion.

    Import from ``ophyd_electrochemistry.sequences.run_file``. The optional HDF5
    dependency is not loaded by importing the pure sequence models.
    """

    def __init__(self, file: Any) -> None:
        self._file = file
        self._finalized = False

    @classmethod
    def create(
        cls,
        path: _Path,
        *,
        measurement_id: str,
        equipment: Mapping[str, object],
        sequence: Sequence,
        limits: SafetyLimits,
        start_mode: str,
        sample_id: str | None = None,
        notes: str | None = None,
        run_sheet_row: Mapping[str, object] | None = None,
        extra: Mapping[str, object] | None = None,
    ) -> "RunFile":
        """Create exclusively; validate metadata before creating the path.

        Optional sample ID and notes are stored as empty strings when absent.
        Sequence safety validation and hardware approval belong to the caller.
        """
        _text(measurement_id, "measurement_id")
        if not measurement_id.strip():
            raise ValidationError("measurement_id must be nonempty")
        if start_mode not in ("immediate", "external"):
            raise ValidationError("start_mode must be immediate or external")
        equipment_values = _equipment(equipment)
        created_utc = _utc_now()
        run_values = {
            "start_mode": start_mode,
            "sample_id": _text("" if sample_id is None else sample_id, "sample_id"),
            "notes": _text("" if notes is None else notes, "notes"),
            "started_utc": created_utc,
            "finished_utc": "",
            "outcome": "incomplete",
            "outcome_detail": "",
        }
        metadata = {
            "sequence": _json(to_dict(sequence), "sequence"),
            "safety_limits": _json(to_dict(limits), "safety_limits"),
            "run_sheet_row": _json({} if run_sheet_row is None else run_sheet_row, "run_sheet_row"),
            "extra": _json({} if extra is None else extra, "extra"),
        }
        # HDF5's exclusive mode handles races; never use a check followed by "w".
        file = h5py.File(path, "x", libver=_LIBVER)
        try:
            file.attrs.update(
                schema=SCHEMA,
                measurement_id=measurement_id,
                created_utc=created_utc,
                package_version=__version__,
            )
            file.create_group("equipment").attrs.update(equipment_values)
            run = file.create_group("run")
            run.attrs.update(run_values)
            for name, value in metadata.items():
                run.create_dataset(name, data=value, dtype=h5py.string_dtype("utf-8"))
            readout = file.create_group("readout")
            for index, name in enumerate(READOUT_FIELDS):
                dataset = readout.create_dataset(
                    name,
                    shape=(0,),
                    maxshape=(None,),
                    chunks=(1024,),
                    compression="gzip",
                    dtype="f8" if index < 3 else "i4",
                )
                dataset.attrs["units"] = _UNITS[name]
            events = file.create_group("events")
            for name in _EVENT_FIELDS:
                dtype = (
                    h5py.string_dtype("utf-8")
                    if name in ("kind", "detail")
                    else ("f8" if name == "t_s" else "i4")
                )
                events.create_dataset(
                    name, shape=(0,), maxshape=(None,), chunks=(128,), dtype=dtype
                )
            # SWMR forbids creating objects from here on; everything exists now.
            file.swmr_mode = True
            file.flush()
        except BaseException:
            file.close()
            raise
        return cls(file)

    def _writable(self) -> None:
        if not self._file.id.valid:
            raise RuntimeError("run file is closed")
        if self._finalized:
            raise RuntimeError("run file is finalized")

    def append_readout(self, chunk: Mapping[str, ArrayLike]) -> None:
        """Append all seven equal-length 1-D fields, then flush.

        Float readings preserve NaN/Inf if supplied by the acquisition backend;
        integer fields reject overflow and fractional values instead of coercing.
        """
        self._writable()
        if set(chunk) != set(READOUT_FIELDS):
            raise ValidationError(f"chunk must contain exactly {READOUT_FIELDS}")
        arrays = {name: _array(chunk[name], name) for name in READOUT_FIELDS}
        if len({len(value) for value in arrays.values()}) != 1:
            raise ValidationError("readout fields must have equal lengths")
        self._append("readout", arrays)

    def _append(self, group_name: str, values: Mapping[str, ArrayLike]) -> None:
        group = self._file[group_name]
        start = len(group["t_s"])
        count = len(np.asarray(values["t_s"]))
        for name, value in values.items():
            dataset = group[name]
            dataset.resize((start + count,))
            dataset[start:] = value
        self._file.flush()

    def append_event(
        self, t_s: float, step_index: int, repeat_index: int, kind: str, detail: str = ""
    ) -> None:
        """Append one event; text is UTF-8 and is never silently truncated."""
        self._writable()
        if kind not in _EVENT_KINDS:
            raise ValidationError(f"kind must be one of {sorted(_EVENT_KINDS)}")
        values: dict[str, ArrayLike] = {
            "t_s": _array([t_s], "t_s"),
            "step_index": _array([step_index], "step_index"),
            "repeat_index": _array([repeat_index], "repeat_index"),
            "kind": [_text(kind, "kind")],
            "detail": [_text(detail, "detail")],
        }
        self._append("events", values)

    def set_time_origin(self, epoch_s: float) -> None:
        """Set the UTC anchor for relative readout times exactly once."""
        self._writable()
        attrs = self._file["run"].attrs
        if "time_origin_epoch_s" in attrs:
            raise RuntimeError("time origin is already set")
        epoch_s = number(epoch_s, "time_origin_epoch_s")
        utc = datetime.fromtimestamp(epoch_s, UTC).isoformat(timespec="microseconds")
        attrs["time_origin_epoch_s"] = epoch_s
        attrs["time_origin_utc"] = utc.replace("+00:00", "Z")
        self._file.flush()

    def finalize(self, outcome: str, detail: str = "") -> None:
        """Record an explicit terminal outcome and prevent further mutations."""
        self._writable()
        if outcome not in _OUTCOMES:
            raise ValidationError(f"outcome must be one of {sorted(_OUTCOMES)}")
        _text(detail, "outcome_detail")
        attrs = self._file["run"].attrs
        attrs["finished_utc"] = _utc_now()
        attrs["outcome_detail"] = detail
        attrs["outcome"] = outcome
        self._file.flush()
        self._finalized = True

    def close(self) -> None:
        """Close idempotently; leave unfinalized files marked incomplete."""
        self._file.close()

    def __enter__(self) -> "RunFile":
        self._writable()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def _plain(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, np.generic):
        return value.item()
    return value


def _whole_records(fields: dict[str, Any]) -> dict[str, Any]:
    # A live writer extends the fields one after another, so a SWMR snapshot can
    # catch them at different lengths (seen in a writer/reader race, 3 Nov).
    size = min(len(value) for value in fields.values())
    return {name: value[:size] for name, value in fields.items()}


def read_run_file(path: _Path) -> dict[str, Any]:
    """Return detached arrays, decoded UTF-8 and parsed JSON metadata.

    ``events`` is a dict of parallel arrays (kind/detail arrays contain str).
    Safe from any process while the writer appends, and after a writer crash;
    a snapshot taken mid-append returns only the records complete in every field.
    """
    with h5py.File(path, "r", libver=_LIBVER, swmr=True) as file:
        if file.attrs.get("schema") != SCHEMA:
            raise ValidationError("unsupported run-file schema")
        result = {
            "attrs": {key: _plain(value) for key, value in file.attrs.items()},
            "equipment": {key: _plain(value) for key, value in file["equipment"].attrs.items()},
            "run": {key: _plain(value) for key, value in file["run"].attrs.items()},
            "readout": _whole_records({name: file["readout"][name][:] for name in READOUT_FIELDS}),
            "events": _whole_records(
                {
                    name: (
                        file["events"][name].asstr()[:]
                        if name in ("kind", "detail")
                        else file["events"][name][:]
                    )
                    for name in _EVENT_FIELDS
                }
            ),
        }
        for name in ("sequence", "safety_limits", "run_sheet_row", "extra"):
            result[name] = json.loads(file["run"][name].asstr()[()])
    return result


def repair_run_file(path: _Path, destination: _Path) -> None:
    """Copy a run file into a new file that any HDF5 1.10+ tool can open.

    Use after a writer crash. The source is only read; the copy keeps every
    flushed record and the ``incomplete`` outcome. ``destination`` must not exist.
    """
    with (
        h5py.File(path, "r", libver=_LIBVER, swmr=True) as source,
        h5py.File(destination, "x", libver=_LIBVER) as target,
    ):
        if source.attrs.get("schema") != SCHEMA:
            raise ValidationError("unsupported run-file schema")
        target.attrs.update(source.attrs)
        for name in source:
            source.copy(source[name], target, name=name)
