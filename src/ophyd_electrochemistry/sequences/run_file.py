"""Incremental per-run HDF5 files (install the ``experiment`` extra).

Every append and finalization flushes the file. Files are written in HDF5 SWMR
mode (single writer, multiple readers), so ``read_run_file(path)`` returns a
fresh snapshot from any process while a run is in progress, e.g. a notebook
plotting a run written by a QueueServer worker. Keep run files on a local
disk while they are written: SWMR is not reliable on network file systems.

Layout (schema ``ophyd-electrochemistry.run-file.v2``, "NeX-ish": NeXus
conventions where they cost little, not NeXus compliance). Every value carries a
``description`` attribute and, where physical, ``units``::

    /entry (NXentry)        title, measurement_id, start_time
      run                   status attributes; start_mode, notes, time origin,
                            sequence, safety limits, run-sheet row, extra (JSON)
      sample (NXsample)     name, session_description, the session's [sample]
      instrument (NXinstrument)
                            identity, runtime, clock, wiring, digital_io, limits,
                            timing, program, source_<n>, measure_<n>, ... groups
      session               the session file (text, hash) and resolved settings
      provenance            Bluesky uid and scan_id, run sheet, host, software
      readout (NXdata)      t_s, voltage_v (signal), current_a, indices, statuses
      events                t_s, step_index, repeat_index, kind, detail

``read_run_file`` returns the same flat dict for v1 (``/equipment``, ``/run``,
``/readout``, ``/events`` at the root) and v2 files, with ``sample``,
``session`` and ``provenance`` empty for v1.

The file format is pinned to HDF5 1.10 (the minimum for SWMR), so HDF5 1.10+
tools read it. Every object exists before SWMR mode starts. SWMR's documented
guarantees cover appends to the numeric readout datasets and writes to existing
numeric datasets (the v2 time origin). Two updates are outside them and are
covered only by a cross-process stress test with HDF5 2.0: the UTF-8 event
datasets, and the run status attributes (outcome, finished time).

A killed writer (e.g. a crashed Jupyter kernel) leaves the file flagged as
open for writing. ``read_run_file`` still reads everything flushed before the
crash, with outcome ``incomplete``; other HDF5 tools refuse the file until it
is copied with ``repair_run_file`` (or cleared with HDF5's ``h5clear``).
Closing, including context-manager exception cleanup, preserves received data.
Flush is not an fsync or a power-loss guarantee.
"""

import functools
import json
import math
import platform
import socket
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata as importlib_metadata
from os import PathLike
from pathlib import Path
from types import TracebackType
from typing import Any

import h5py  # type: ignore[import-untyped]
import numpy as np
from numpy.typing import ArrayLike, NDArray

from .. import __version__
from ..exceptions import ValidationError
from ..validation import number
from . import run_file_fields as fields
from .models import SafetyLimits, Sequence, expected_records, nominal_duration_s, to_dict

SCHEMA = "ophyd-electrochemistry.run-file.v2"
SCHEMA_V1 = "ophyd-electrochemistry.run-file.v1"
READOUT_FIELDS: tuple[str, ...] = (
    "t_s",
    "voltage_v",
    "current_a",
    "step_index",
    "repeat_index",
    "source_status",
    "measure_status",
)
_UNITS = {name: units for name, (units, _) in fields.READOUT.items()}
_EVENT_FIELDS = ("t_s", "step_index", "repeat_index", "kind", "detail")
_EVENT_KINDS = {"step_start", "cutoff", "stop", "abort", "error"}
_OUTCOMES = {"completed", "stopped_cutoff", "aborted", "error"}
_JSON_DATASETS = ("sequence", "safety_limits", "run_sheet_row", "extra")
_Path = str | PathLike[str]
# HDF5 1.10 file format: the minimum for SWMR, readable by HDF5 1.10+ tools.
_LIBVER = ("v110", "v110")
_STR = h5py.string_dtype("utf-8")
# Packages whose versions are recorded when installed (absent ones are left out).
_PACKAGES = (
    "ophyd-electrochemistry",
    "bluesky",
    "ophyd",
    "ophyd-async",
    "event-model",
    "bluesky-queueserver",
    "bluesky-queueserver-api",
    "pyvisa",
    "pyvisa-py",
    "numpy",
    "h5py",
)


@dataclass(frozen=True)
class Layout:
    """Group paths of one schema version (``RunFile.layout``)."""

    run: str
    readout: str
    events: str


_LAYOUTS = {
    SCHEMA_V1: Layout("run", "readout", "events"),
    SCHEMA: Layout("entry/run", "entry/readout", "entry/events"),
}


def _layout(file: Any) -> Layout:
    layout = _LAYOUTS.get(_plain(file.attrs.get("schema")))
    if layout is None:
        raise ValidationError("unsupported run-file schema")
    return layout


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


def _json(value: object, name: str) -> str:
    try:
        return json.dumps(value, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValidationError(f"{name} must be JSON-safe: {exc}") from exc


def _name(key: str, kind: str) -> str:
    """A key usable as an HDF5 dataset name ("." and ".." name the group itself)."""
    _text(key, f"{kind} key")
    if not key or "/" in key or key.startswith("."):
        raise ValidationError(f"{kind} key {key!r} is not a valid name")
    return key


def _equipment(settings: Mapping[str, object]) -> dict[str, object]:
    result = dict(settings)
    for key, value in result.items():
        _name(key, "equipment")
        if not isinstance(value, (str, int, float, bool)):
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


# A value to write: (group path, dataset name, value, units, description).
_Item = tuple[str, str, Any, str, str]


def _scalar(value: object, name: str) -> Any:
    """A validated scalar for HDF5: str, bool, int64/uint64 or float64; else JSON text.

    None is not stored (the dataset is left out); mappings and lists become JSON.
    """
    if isinstance(value, str):
        return _text(value, name)
    if isinstance(value, bool | np.bool_):
        return np.bool_(value)
    if isinstance(value, int | np.integer):
        value = int(value)
        if not -(2**63) <= value < 2**64:
            raise ValidationError(f"{name} exceeds HDF5 integer range")
        return np.int64(value) if value < 2**63 else np.uint64(value)
    if isinstance(value, float | np.floating):
        if not math.isfinite(value):
            raise ValidationError(f"{name} must be finite")
        return np.float64(value)
    return _text(_json(value, name), name)


def _items(
    group: str, values: Mapping[str, object], table: Mapping[str, tuple[str, str]], kind: str
) -> list[_Item]:
    items = []
    for key, value in values.items():
        _name(key, kind)
        if value is None:
            continue
        units, description = table.get(key, ("", ""))
        items.append((group, key, _scalar(value, f"{kind}.{key}"), units, description))
    return items


def _header_comment(text: str) -> str:
    """The leading ``#`` comment block of a session file."""
    lines: list[str] = []
    for line in text.lstrip("﻿").splitlines():
        stripped = line.strip()
        if not stripped.startswith("#"):
            if stripped or lines:
                break
            continue
        lines.append(stripped[1:].strip())
    return "\n".join(lines)


def _v2_items(
    *,
    measurement_id: str,
    created_utc: str,
    start_mode: str,
    sample_id: str,
    notes: str,
    sequence: Sequence,
    json_values: Mapping[str, str],
    equipment: Mapping[str, object],
    sample: Mapping[str, object] | None,
    session: Mapping[str, object] | None,
    provenance: Mapping[str, object] | None,
) -> list[_Item]:
    """Every scalar dataset of a v2 file, validated before the file exists."""
    items: list[_Item] = [
        ("entry", "title", measurement_id, "", "measurement ID (run-sheet row)"),
        ("entry", "measurement_id", measurement_id, "", "measurement ID (run-sheet row)"),
        ("entry", "start_time", created_utc, "", "file created (ISO 8601 UTC)"),
    ]
    run = {
        "start_mode": start_mode,
        "notes": notes,
        "expected_records": expected_records(sequence),
        "nominal_duration_s": float(nominal_duration_s(sequence)),
    }
    items += _items("entry/run", run, fields.RUN_DATASETS, "run")
    for name, value in json_values.items():
        items.append(("entry/run", name, value, "", fields.RUN_DATASETS[name][1]))

    session = dict(session or {})
    provenance = dict(provenance or {})
    # Read back as JSON objects: a mapping or nothing (Codex, brief 17).
    for kind, values, key in (
        ("session", session, "settings"),
        ("provenance", provenance, "software"),
    ):
        if values.get(key) is not None and not isinstance(values[key], Mapping):
            raise ValidationError(f"{kind}.{key} must be a mapping")
    table = fields.check_sample(session.pop("sample", None) or {})
    table.update(fields.check_sample(dict(sample or {})))
    text = session.get("text")
    header = session.pop("description", None)
    if header is None and isinstance(text, str):
        header = _header_comment(text)
    items.append(("entry/sample", "name", sample_id, "", fields.SAMPLE_FIXED["name"]))
    if header:
        items.append(
            (
                "entry/sample",
                "session_description",
                _text(str(header), "session description"),
                "",
                fields.SAMPLE_FIXED["session_description"],
            )
        )
    for key, entry in table.items():
        units = fields.sample_units(key)
        note = "from the session's [sample] table"
        items.append(("entry/sample", key, _scalar(entry, key), units, note))

    for key, setting in equipment.items():
        group, name, units, description = fields.instrument_place(key, dict(equipment))
        items.append((f"entry/instrument/{group}", name, _scalar(setting, key), units, description))
    items += _items("entry/session", session, fields.SESSION, "session")
    items += _items("entry/provenance", provenance, fields.PROVENANCE, "provenance")
    paths = [(group, name) for group, name, *_ in items]
    if len(paths) != len(set(paths)):
        duplicate = next(p for p in paths if paths.count(p) > 1)
        raise ValidationError(f"two values for {'/'.join(duplicate)}")
    return items


class RunFile:
    """One writer per run; closing never implies successful completion.

    Import from ``ophyd_electrochemistry.sequences.run_file``. The optional HDF5
    dependency is not loaded by importing the pure sequence models. Works on v1
    and v2 files (``oe-recover-run`` appends to either).
    """

    def __init__(self, file: Any) -> None:
        self._file = file
        self.layout = _layout(file)
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
        sample: Mapping[str, object] | None = None,
        session: Mapping[str, object] | None = None,
        provenance: Mapping[str, object] | None = None,
    ) -> "RunFile":
        """Create exclusively a v2 file; validate all metadata before creating the path.

        ``sample`` is a ``[sample]`` table (see ``run_file_fields.check_sample``).
        ``session`` holds ``format``, ``path``, ``sha256``, ``text``, ``settings``
        and optionally ``sample`` (its ``[sample]`` table) and ``description``
        (default: the text's header comment), stored under ``/entry/sample``.
        ``provenance`` values are scalars or JSON-safe mappings; None leaves a
        value out. Optional sample ID and notes are stored as empty strings when
        absent. Sequence safety validation and hardware approval belong to the caller.
        """
        _text(measurement_id, "measurement_id")
        if not measurement_id.strip():
            raise ValidationError("measurement_id must be nonempty")
        if start_mode not in ("immediate", "external"):
            raise ValidationError("start_mode must be immediate or external")
        equipment_values = _equipment(equipment)
        created_utc = _utc_now()
        json_values = {
            "sequence": _json(to_dict(sequence), "sequence"),
            "safety_limits": _json(to_dict(limits), "safety_limits"),
            "run_sheet_row": _json(dict(run_sheet_row or {}), "run_sheet_row"),
            "extra": _json(dict(extra or {}), "extra"),
        }
        items = _v2_items(
            measurement_id=measurement_id,
            created_utc=created_utc,
            start_mode=start_mode,
            sample_id=_text("" if sample_id is None else sample_id, "sample_id"),
            notes=_text("" if notes is None else notes, "notes"),
            sequence=sequence,
            json_values=json_values,
            equipment=equipment_values,
            sample=sample,
            session=session,
            provenance=provenance,
        )
        # HDF5's exclusive mode handles races; never use a check followed by "w".
        file = h5py.File(path, "x", libver=_LIBVER)
        try:
            _write_v2(file, created_utc, items, equipment_values)
            # SWMR forbids creating objects from here on; everything exists now.
            file.swmr_mode = True
            file.flush()
        except BaseException:
            # This call created the file exclusively and wrote no data to it.
            file.close()
            Path(path).unlink(missing_ok=True)
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
        self._append(self.layout.readout, arrays)

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
        self._append(self.layout.events, values)

    def set_time_origin(self, epoch_s: float) -> None:
        """Set the UTC anchor for relative readout times exactly once."""
        self._writable()
        epoch_s = number(epoch_s, "time_origin_epoch_s")
        run = self._file[self.layout.run]
        if self.layout.run == "run":  # v1: attributes
            if "time_origin_epoch_s" in run.attrs:
                raise RuntimeError("time origin is already set")
            utc = datetime.fromtimestamp(epoch_s, UTC).isoformat(timespec="microseconds")
            run.attrs["time_origin_epoch_s"] = epoch_s
            run.attrs["time_origin_utc"] = utc.replace("+00:00", "Z")
        else:  # v2: a numeric dataset created NaN before SWMR mode
            dataset = run["time_origin_epoch_s"]
            if not math.isnan(dataset[()]):
                raise RuntimeError("time origin is already set")
            dataset[()] = epoch_s
        self._file.flush()

    def finalize(self, outcome: str, detail: str = "") -> None:
        """Record an explicit terminal outcome and prevent further mutations."""
        self._writable()
        if outcome not in _OUTCOMES:
            raise ValidationError(f"outcome must be one of {sorted(_OUTCOMES)}")
        _text(detail, "outcome_detail")
        attrs = self._file[self.layout.run].attrs
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


def _group(file: Any, path: str, nx_class: str, description: str = "") -> Any:
    group = file.require_group(path)
    group.attrs["NX_class"] = nx_class
    if description:
        group.attrs["description"] = description
    return group


def _write_v2(
    file: Any, created_utc: str, items: list[_Item], equipment: Mapping[str, object]
) -> None:
    file.attrs.update(
        schema=SCHEMA,
        NX_class="NXroot",
        default="entry",
        file_time=created_utc,
        creator="ophyd-electrochemistry",
        creator_version=__version__,
        HDF5_Version=h5py.version.hdf5_version,
        h5py_version=h5py.version.version,
    )
    entry = _group(file, "entry", "NXentry", "one sequence run on the Keithley 2460")
    entry.attrs["default"] = "readout"
    run = _group(file, "entry/run", "NXcollection", "run status, time origin and the request")
    run.attrs.update(started_utc=created_utc, finished_utc="", outcome="incomplete")
    run.attrs["outcome_detail"] = ""
    run.attrs["descriptions"] = _json({k: d for k, (_, d) in fields.RUN_ATTRS.items()}, "run")
    _group(file, "entry/sample", "NXsample", "the cell")
    _group(file, "entry/instrument", "NXinstrument", "Keithley 2460 and its sequencer runtime")
    _group(file, "entry/session", "NXcollection", "the environment the run was confirmed in")
    _group(file, "entry/provenance", "NXcollection", "where the run came from")
    for path, name, value, units, description in items:
        if path not in file:
            leaf = path.rsplit("/", 1)[1]
            group = _group(file, path, "NXcollection", fields.GROUPS.get(leaf, ""))
            if leaf.startswith(("source_", "measure_")) and leaf[-1].isdigit():
                kind, number = leaf.rsplit("_", 1)
                group.attrs["key_prefix"] = f"{leaf}_"
                group.attrs["description"] = f"{kind} configuration {number} of the program"
        dataset = file[path].create_dataset(
            name, data=value, dtype=_STR if isinstance(value, str) else None
        )
        dataset.attrs["description"] = description
        if units:
            dataset.attrs["units"] = units
    origin = run.create_dataset("time_origin_epoch_s", data=np.float64(np.nan))
    units, description = fields.RUN_DATASETS["time_origin_epoch_s"]
    origin.attrs.update(units=units, description=description)

    readout = _group(file, "entry/readout", "NXdata", "every reading, in order")
    readout.attrs.update(
        signal="voltage_v",
        auxiliary_signals=np.array(["current_a"], dtype=_STR),
        axes=np.array(["t_s"], dtype=_STR),
        t_s_indices=0,
    )
    for index, name in enumerate(READOUT_FIELDS):
        dataset = readout.create_dataset(
            name,
            shape=(0,),
            maxshape=(None,),
            chunks=(1024,),
            compression="gzip",
            dtype="f8" if index < 3 else "i4",
        )
        units, description = fields.READOUT[name]
        dataset.attrs["units"] = units
        dataset.attrs["description"] = description
    readout["t_s"].attrs["long_name"] = "time since start (s)"
    events = _group(file, "entry/events", "NXcollection", "step starts, cutoffs, stop, errors")
    for name in _EVENT_FIELDS:
        dtype = _STR if name in ("kind", "detail") else ("f8" if name == "t_s" else "i4")
        dataset = events.create_dataset(
            name, shape=(0,), maxshape=(None,), chunks=(128,), dtype=dtype
        )
        units, description = fields.EVENTS[name]
        if units:
            dataset.attrs["units"] = units
        dataset.attrs["description"] = description


def _plain(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, np.generic):
        return value.item()
    return value


def _value(dataset: Any) -> Any:
    if h5py.check_string_dtype(dataset.dtype) is not None:
        return dataset.asstr()[()]
    return _plain(dataset[()])


def _whole_records(fields: dict[str, Any]) -> dict[str, Any]:
    # A live writer extends the fields one after another, so a SWMR snapshot can
    # catch them at different lengths (seen in a writer/reader race, 3 Nov).
    size = min(len(value) for value in fields.values())
    return {name: value[:size] for name, value in fields.items()}


def _flat(group: Any) -> dict[str, Any]:
    return {name: _value(item) for name, item in group.items() if isinstance(item, h5py.Dataset)}


def _instrument(group: Any) -> dict[str, Any]:
    """The flat equipment dict back from ``/entry/instrument``'s groups."""
    result: dict[str, Any] = {}
    for sub in group.values():
        prefix = _plain(sub.attrs.get("key_prefix", ""))
        for name, value in _flat(sub).items():
            result[f"{prefix}{name}"] = value
    return result


def _read_v2(file: Any) -> dict[str, Any]:
    entry = file["entry"]
    run_values = _flat(entry["run"])
    origin = run_values.pop("time_origin_epoch_s")
    run = {
        "start_mode": run_values["start_mode"],
        "sample_id": _value(entry["sample/name"]),
        "notes": run_values["notes"],
        "expected_records": run_values["expected_records"],
        "nominal_duration_s": run_values["nominal_duration_s"],
        **{key: _plain(entry["run"].attrs[key]) for key in fields.RUN_ATTRS},
    }
    if not math.isnan(origin):
        utc = datetime.fromtimestamp(origin, UTC).isoformat(timespec="microseconds")
        run["time_origin_epoch_s"] = origin
        run["time_origin_utc"] = utc.replace("+00:00", "Z")
    return {
        "attrs": {
            "schema": SCHEMA,
            "measurement_id": _value(entry["measurement_id"]),
            "created_utc": _plain(file.attrs["file_time"]),
            "package_version": _plain(file.attrs["creator_version"]),
        },
        "equipment": _instrument(entry["instrument"]),
        "run": run,
        "sample": _flat(entry["sample"]),
        "session": _flat(entry["session"]),
        "provenance": _flat(entry["provenance"]),
    }


def read_run_file(path: _Path) -> dict[str, Any]:
    """Return detached arrays, decoded UTF-8 and parsed JSON metadata.

    The same flat dict for v1 and v2 files: ``attrs`` (schema, measurement_id,
    created_utc, package_version), ``equipment``, ``run``, ``readout``, ``events``,
    ``sequence``, ``safety_limits``, ``run_sheet_row``, ``extra``, and (empty for v1)
    ``sample``, ``session``, ``provenance``. ``events`` is a dict of parallel
    arrays (kind/detail arrays contain str). Safe from any process while the
    writer appends, and after a writer crash; a snapshot taken mid-append returns
    only the records complete in every field.
    """
    with h5py.File(path, "r", libver=_LIBVER, swmr=True) as file:
        layout = _layout(file)
        if layout.run == "run":
            result: dict[str, Any] = {
                "attrs": {key: _plain(value) for key, value in file.attrs.items()},
                "equipment": {k: _plain(v) for k, v in file["equipment"].attrs.items()},
                "run": {key: _plain(value) for key, value in file["run"].attrs.items()},
                "sample": {},
                "session": {},
                "provenance": {},
            }
        else:
            result = _read_v2(file)
        readout, events = file[layout.readout], file[layout.events]
        result["readout"] = _whole_records({name: readout[name][:] for name in READOUT_FIELDS})
        result["events"] = _whole_records(
            {
                name: (events[name].asstr()[:] if name in ("kind", "detail") else events[name][:])
                for name in _EVENT_FIELDS
            }
        )
        for name in _JSON_DATASETS:
            result[name] = json.loads(file[layout.run][name].asstr()[()])
        for group, name in (("session", "settings"), ("provenance", "software")):
            if isinstance(result[group].get(name), str):
                result[group][name] = json.loads(result[group][name])
    return result


def repair_run_file(path: _Path, destination: _Path) -> None:
    """Copy a run file into a new file that any HDF5 1.10+ tool can open.

    Use after a writer crash. The source is only read; the copy keeps every
    flushed record, the layout (v1 or v2) and the ``incomplete`` outcome.
    ``destination`` must not exist.
    """
    with (
        h5py.File(path, "r", libver=_LIBVER, swmr=True) as source,
        h5py.File(destination, "x", libver=_LIBVER) as target,
    ):
        _layout(source)
        target.attrs.update(source.attrs)
        for name in source:
            source.copy(source[name], target, name=name)


@functools.cache
def _source_revision() -> str | None:
    root = Path(__file__).resolve().parents[3]
    if not (root / ".git").exists():
        return None
    try:
        run = functools.partial(
            subprocess.run, cwd=root, capture_output=True, text=True, timeout=5, check=True
        )
        head = run(["git", "rev-parse", "HEAD"]).stdout.strip()
        dirty = run(["git", "status", "--porcelain", "--untracked-files=no"]).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return f"{head}+dirty" if dirty else head


def host_provenance() -> dict[str, Any]:
    """Host, platform, git revision (if a checkout) and installed package versions."""
    software: dict[str, str] = {
        "python": platform.python_version(),
        "hdf5": h5py.version.hdf5_version,
    }
    for package in _PACKAGES:
        try:
            software[package] = importlib_metadata.version(package)
        except importlib_metadata.PackageNotFoundError:
            continue
    return {
        "host": socket.gethostname(),
        "platform": platform.platform(),
        "source_revision": _source_revision(),
        "software": software,
    }
