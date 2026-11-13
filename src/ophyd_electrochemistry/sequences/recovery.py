"""Fold an ``oe-archive-retained`` archive into a copy of the run's file.

After a host crash, a sleep or a lost network link, the run file stops at the
last page the host wrote (outcome ``incomplete``, ``error`` or ``aborted``)
while the 2460 finishes the run, and ``oe-archive-retained`` saves every record
the instrument kept (raw fields, npz). ``recover_run_file`` recompiles the run's
own program from the file's metadata, replays the whole archive through the
same ``Replayer`` the live backend uses, checks that the replay reproduces every
reading and event already in the file, and writes a new file with the file's
data plus the rest.

Both inputs are only read (their SHA-256 is checked before and after). The
outcome is not changed: the archive holds no instrument event log, so a
recovered run is never reported as completed. The replay's prediction, the
inputs and their hashes are stored in the run group's ``recovery`` dataset (JSON;
``/run`` in v1 files, ``/entry/run`` in v2; the layout is kept) and summarised in
``outcome_detail``. Concept from Codex's one-off recovery of ``store-d`` (13 Nov).
"""

import argparse
import hashlib
import json
import math
import os
import uuid
from datetime import UTC, datetime
from os import PathLike
from pathlib import Path
from typing import Any

import h5py  # type: ignore[import-untyped]
import numpy as np

from ..exceptions import ValidationError
from ..keithley.k2460.sequencer import Replayer, SequencerProfile, compile_program
from .models import limits_from_dict, sequence_from_dict
from .run_file import READOUT_FIELDS, RunFile, read_run_file, repair_run_file

RECOVERABLE = ("incomplete", "error", "aborted")
RAW_FIELDS = ("seconds", "fraction", "reading", "source", "status", "source_status")
# Saved and replayed times of the same records. The time origin is a float64
# epoch (resolution ~0.24 us), so this is tight, yet far below any sample spacing.
TIME_ATOL_S = 1e-5
_EVENT_FIELDS = ("t_s", "step_index", "repeat_index", "kind", "detail")
_PREDICTION = {"completed": "completed", "stop": "stopped_cutoff", "running": "unfinished"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _same(a: Any, b: Any) -> bool:
    """Equal shape and values; NaN equals NaN in float fields."""
    a, b = np.asarray(a), np.asarray(b)
    return a.shape == b.shape and bool(np.array_equal(a, b, equal_nan=a.dtype.kind == "f"))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def default_destination(run_path: Path) -> Path:
    """``<id>-recovered_<stamp>.h5`` beside ``<id>_<stamp>.h5``."""
    stem, sep, stamp = run_path.name.rpartition("_")
    name = f"{stem}-recovered_{stamp}" if sep else f"{run_path.stem}-recovered{run_path.suffix}"
    return run_path.with_name(name)


def _load_archive(path: Path) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    with np.load(path, allow_pickle=False) as raw:
        missing = [name for name in (*RAW_FIELDS, "metadata") if name not in raw.files]
        _require(not missing, f"{path.name}: not an oe-archive-retained archive (no {missing})")
        arrays = {name: np.asarray(raw[name]) for name in RAW_FIELDS}
        metadata = json.loads(str(raw["metadata"]))
    _require(len({len(a) for a in arrays.values()}) == 1, f"{path.name}: unequal field lengths")
    return arrays, metadata


def recover_run_file(
    run_path: str | PathLike[str],
    archive_path: str | PathLike[str],
    destination: str | PathLike[str] | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Write ``destination`` (default: :func:`default_destination`); return it and the
    provenance. Raises ValidationError, and leaves no file, unless the archive
    belongs to the run: same instrument and runtime, the program recompiles to the
    recorded settings, its records carry the run's absolute instrument times (the
    file's UTC time origin plus its clock offset), and the replay reproduces every
    saved reading and event. Two limits: the archive must hold the whole run (from
    record 1), and a file with events the replay cannot produce (an instrument
    error, an abort) is refused."""
    source, archive = Path(run_path), Path(archive_path)
    target = Path(destination) if destination is not None else default_destination(source)
    _require(target.resolve() not in (source.resolve(), archive.resolve()), "use a new output file")
    _require(not target.exists(), f"{target} exists")
    hashes = (_sha256(source), _sha256(archive))

    saved = read_run_file(source)
    run, equipment, original = saved["run"], saved["equipment"], saved["readout"]
    _require(
        run["outcome"] in RECOVERABLE,
        f"outcome {run['outcome']!r}: only {', '.join(RECOVERABLE)} runs lack records",
    )
    prefix = len(original["t_s"])
    _require(prefix > 0, "the run file has no readings to align the archive with")

    program = compile_program(
        sequence_from_dict(saved["sequence"]),
        limits_from_dict(saved["safety_limits"]),
        SequencerProfile.from_settings(equipment),
        external_start=bool(equipment["external_start"]),
    )
    differ = sorted(k for k, v in program.settings().items() if equipment.get(k) != v)
    _require(not differ, f"the recompiled program differs from the run's settings: {differ}")

    raw, metadata = _load_archive(archive)
    count = len(raw["reading"])
    for key in ("identity", "runtime_build"):
        _require(
            metadata.get(key) == equipment.get(key),
            f"archive {key} {metadata.get(key)!r} is not the run's {equipment.get(key)!r}",
        )
    # Records are acknowledged only once the host has concluded a run or archived
    # them, so the archive of a run the host lost starts at record 1.
    _require(metadata.get("first_record") == 1, "the archive does not start at record 1")
    _require(count == metadata["status"][2], "the archive does not hold every record")
    _require(count >= prefix, f"the archive holds {count} records, the file {prefix}")

    # The file's times count from the runtime's OE_T0 event, whose instrument time
    # is the UTC time origin plus the measured clock offset. Absolute times tie the
    # archive to this run: matching readings alone could come from another run of
    # the same sequence (Codex, brief 15).
    origin, offset = run.get("time_origin_epoch_s"), equipment.get("clock_offset_s")
    _require(
        origin is not None and offset is not None,
        "the run file has no time origin or clock offset to check the archive against",
    )
    t0 = float(origin) + float(offset)
    t0_s = math.floor(t0)
    times = (raw["seconds"] - t0_s) + (raw["fraction"] - (t0 - t0_s))
    replay = Replayer(program)
    columns = (times, raw["reading"], raw["source"], raw["status"], raw["source_status"])
    readout, events = replay.feed(*columns)

    for name in READOUT_FIELDS:
        replayed, kept = readout[name][:prefix], np.asarray(original[name])
        same = (
            bool(np.allclose(replayed, kept, rtol=0, atol=TIME_ATOL_S))
            if name == "t_s"
            else _same(replayed, kept)
        )
        _require(same, f"the archive does not reproduce the file's {name}")
    saved_events = list(zip(*(saved["events"][k] for k in _EVENT_FIELDS), strict=True))
    replayed_events = [(float(t), int(s), int(r), str(k), str(d)) for t, s, r, k, d in events]
    _require(
        len(saved_events) <= len(replayed_events)
        and all(
            abs(a[0] - b[0]) <= TIME_ATOL_S and a[1:] == b[1:]
            for a, b in zip(saved_events, replayed_events, strict=False)
        ),
        "the file's events are not the replay's first events",
    )
    added_events = replayed_events[len(saved_events) :]

    prediction = _PREDICTION.get(replay.predicted, replay.predicted)
    provenance: dict[str, Any] = {
        "recovered_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "run_file": str(source.resolve()),
        "run_file_sha256": hashes[0],
        "archive": str(archive.resolve()),
        "archive_sha256": hashes[1],
        "archive_metadata": metadata,
        "records_in_file": prefix,
        "records_added": count - prefix,
        "events_added": len(added_events),
        "replay_prediction": prediction,
        "time_alignment": "instrument time = time_origin_epoch_s + clock_offset_s + t_s; "
        f"all {prefix} saved times agree within {TIME_ATOL_S} s",
        "outcome_kept": run["outcome"],
    }
    note = (
        f"recovered: {count - prefix} records from {archive.name}; replay predicts "
        f"{prediction}; outcome kept (no instrument log in the archive)"
    )
    detail = f"{run['outcome_detail']}; {note}" if run["outcome_detail"] else note

    _require((_sha256(source), _sha256(archive)) == hashes, "an input changed while it was read")
    # Built under a unique temporary name that only this call creates, so any
    # failure removes it; the destination appears only when everything checked.
    partial = target.with_name(f".{target.name}.{uuid.uuid4().hex}.partial")
    try:
        repair_run_file(source, partial)
        with h5py.File(partial, "r+", libver=("v110", "v110")) as file:
            writer = RunFile(file)  # knows the v1 and v2 group paths
            run_attrs = file[writer.layout.run]
            # A writer killed mid-append can leave one field longer than the rest
            # (read_run_file returns whole records): cut back to them (brief 15).
            for group, fields, size in (
                (writer.layout.readout, READOUT_FIELDS, prefix),
                (writer.layout.events, _EVENT_FIELDS, len(saved_events)),
            ):
                for name in fields:
                    file[group][name].resize((size,))
            recovery = run_attrs.create_dataset(
                "recovery", data=json.dumps(provenance), dtype=h5py.string_dtype("utf-8")
            )
            recovery.attrs["description"] = "oe-recover-run provenance (JSON)"
            run_attrs.attrs["outcome_detail"] = detail
            if count > prefix:
                writer.append_readout({k: v[prefix:] for k, v in readout.items()})
            for event in added_events:
                writer.append_event(*event)
        check = read_run_file(partial)
        for name in READOUT_FIELDS:
            kept = np.concatenate([np.asarray(original[name]), readout[name][prefix:]])
            _require(_same(check["readout"][name], kept), f"read-back of {name} differs")
        _require(
            len(check["events"]["t_s"]) == len(saved_events) + len(added_events),
            "read-back of the events differs",
        )
        _require(check["run"]["outcome"] == run["outcome"], "outcome changed")
        _require(check["equipment"] == equipment, "equipment metadata changed")
        _require(check["attrs"]["schema"] == saved["attrs"]["schema"], "layout changed")
        _require((_sha256(source), _sha256(archive)) == hashes, "an input changed")
        os.link(partial, target)  # fails if the destination appeared meanwhile
    finally:
        partial.unlink(missing_ok=True)
    return target, provenance


def main(argv: list[str] | None = None) -> int:
    """Fold an oe-archive-retained archive into a copy of the run file it belongs to."""
    parser = argparse.ArgumentParser(prog="oe-recover-run", description=main.__doc__)
    parser.add_argument("run_file", type=Path, help="the run's HDF5 file (incomplete or error)")
    parser.add_argument("archive", type=Path, help="retained-<UTC>.npz from oe-archive-retained")
    parser.add_argument("--out", type=Path, help="default: <id>-recovered_<stamp>.h5 beside it")
    args = parser.parse_args(argv)
    try:
        path, provenance = recover_run_file(
            args.run_file.expanduser(),
            args.archive.expanduser(),
            args.out and args.out.expanduser(),
        )
    except ValidationError as exc:
        print(f"oe-recover-run: refused, nothing written: {exc}")
        return 1
    print(
        f"Wrote {path}: {provenance['records_in_file']} records from the file + "
        f"{provenance['records_added']} from the archive, {provenance['events_added']} events "
        f"added; replay predicts {provenance['replay_prediction']}; outcome kept "
        f"({provenance['outcome_kept']})"
    )
    return 0
