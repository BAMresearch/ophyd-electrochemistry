# mypy: disable-error-code="no-untyped-call"

"""QueueServer integration: session file, startup namespace, oe-validate and oe-enqueue.

The RE Manager worker is the one process that owns the instrument. A session
TOML file (``OE_SESSION``) fixes the backend, data directory and safety limits
for every queued run. A queue item names one run-sheet row and the sheet's
SHA-256 at enqueue time; the plan refuses a sheet that changed since.

The session file is where the operator fixes the set-up (instrument, terminals,
data folder) and the operational limits; starting the RE Manager with it and
opening the environment is the confirmation for every run in that session. Each
run is then validated against those limits, not confirmed again. Requires the
``experiment`` and ``queueserver`` extras (and ``visa`` for the 2460).
"""

import argparse
import hashlib
import math
import tomllib
from collections.abc import Callable, Generator, Iterable, Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Literal

from bluesky import RunEngine
from bluesky import plan_stubs as bps
from bluesky.utils import Msg

from ..exceptions import ValidationError
from ..validation import positive, text
from .backend import SequenceBackend, SequenceRequest, SimulatedSequenceBackend
from .callbacks import RunFileWriter
from .flyer import SequenceFlyer, archive_retained
from .models import SafetyLimits, to_dict
from .notebook import run_summary
from .plans import run_sequence
from .run_file_fields import check_sample
from .run_sheet import load_run_sheet

PLAN_NAME = "run_from_sheet"


@dataclass(frozen=True)
class Session:
    """Settings shared by every run of one QueueServer session."""

    backend: Literal["simulator", "k2460"]
    data_dir: Path
    limits: SafetyLimits
    resource: str | None = None
    serial: str | None = None
    terminals: Literal["front", "rear"] = "front"
    start_timeout_s: float | None = None
    poll_period_s: float = 1.0
    sim_speed: float = 1.0
    # The optional [sample] table: descriptive only, written to each run file's
    # /entry/sample; nothing in a run reads it.
    sample: Mapping[str, Any] = field(default_factory=dict)
    # The file as loaded (set by load_session), stored in each run file.
    source_path: Path | None = field(default=None, compare=False, repr=False)
    source_text: str | None = field(default=None, compare=False, repr=False)
    source_sha256: str | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if self.backend not in ("simulator", "k2460"):
            raise ValidationError("backend must be 'simulator' or 'k2460'")
        if self.backend == "k2460":
            text(self.resource, "resource")
        if self.terminals not in ("front", "rear"):
            raise ValidationError("terminals must be 'front' or 'rear'")
        if self.start_timeout_s is not None:
            positive(self.start_timeout_s, "start_timeout_s")
        positive(self.poll_period_s, "poll_period_s")
        if not isinstance(self.limits, SafetyLimits):
            raise ValidationError("limits must be SafetyLimits")
        object.__setattr__(self, "sample", check_sample(dict(self.sample)))

    @property
    def hardware(self) -> bool:
        return self.backend != "simulator"


_SOURCE_FIELDS = {"source_path", "source_text", "source_sha256"}


def load_session(path: str | Path) -> Session:
    """Read a session TOML file: top-level settings, a ``[limits]`` table and an
    optional ``[sample]`` table (text, numbers, true/false; units by key suffix,
    e.g. ``mass_mg``). The bytes read are kept, with their SHA-256, for the run files."""
    raw = Path(path).read_bytes()
    try:
        source_text = raw.decode("utf-8")
        data = tomllib.loads(source_text)
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ValidationError(f"invalid session file: {exc}") from exc
    limits = data.pop("limits", None)
    if not isinstance(limits, dict):
        raise ValidationError("session file needs a [limits] table")
    known = {f.name for f in fields(Session)} - {"limits"} - _SOURCE_FIELDS
    unknown = data.keys() - known
    if unknown:
        raise ValidationError(f"unknown session settings: {', '.join(sorted(unknown))}")
    if "data_dir" not in data:
        raise ValidationError("session file needs data_dir")
    data["data_dir"] = Path(data["data_dir"]).expanduser()
    try:
        return Session(
            limits=SafetyLimits(**limits),
            source_path=Path(path).expanduser().resolve(),
            source_text=source_text,
            source_sha256=hashlib.sha256(raw).hexdigest(),
            **data,
        )
    except TypeError as exc:
        raise ValidationError(f"invalid session file: {exc}") from exc


def session_metadata(session: Session) -> dict[str, Any]:
    """What a run file stores about the session (``RunFile.create(session=...)``).

    A session from ``load_session`` is a TOML file (path, text and hash as loaded);
    one built in a per-run notebook's settings cell has only its resolved settings
    (the executed notebook is kept beside the run file).
    """
    loaded = session.source_text is not None
    return {
        "format": "toml" if loaded else "notebook",
        "path": str(session.source_path) if session.source_path else None,
        "sha256": session.source_sha256,
        "text": session.source_text,
        "settings": {
            "backend": session.backend,
            "data_dir": str(session.data_dir),
            "resource": session.resource,
            "serial": session.serial,
            "terminals": session.terminals,
            "start_timeout_s": session.start_timeout_s,
            "poll_period_s": session.poll_period_s,
            # inf (no waiting) is not JSON: kept as text
            "sim_speed": session.sim_speed
            if math.isfinite(session.sim_speed)
            else str(session.sim_speed),
            "limits": to_dict(session.limits),
        },
        "sample": dict(session.sample),
    }


def sheet_digest(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_request(
    session: Session, run_sheet: str | Path, measurement_id: str, sheet_sha256: str
) -> SequenceRequest:
    """The request for one row, from the sheet exactly as it was enqueued."""
    if sheet_digest(run_sheet) != sheet_sha256:
        raise ValidationError(f"{run_sheet} changed since this run was enqueued; enqueue again")
    runs = load_run_sheet(run_sheet, session.limits)
    if measurement_id not in runs:
        raise ValidationError(f"{measurement_id!r} is not in {run_sheet}")
    return SequenceRequest.from_run_spec(runs[measurement_id], session.limits)


def make_backend(session: Session) -> SequenceBackend:
    if session.backend == "simulator":
        return SimulatedSequenceBackend(speed=session.sim_speed)
    from ..keithley.k2460.sequence_backend import k2460_sequence_backend

    assert session.resource is not None
    return k2460_sequence_backend(
        session.resource, terminals=session.terminals, expected_serial_number=session.serial
    )


def build_namespace(session: Session, *, backend: SequenceBackend | None = None) -> dict[str, Any]:
    """RunEngine, run-file writer, Flyer and the queueable plans for a startup file.

    Opening the environment sets the 2460's output-off mode to HIGH-Z, and fails
    if that cannot be confirmed: operators connect cells only after this.
    """
    engine = RunEngine({})
    writer = RunFileWriter(session.data_dir, session=session_metadata(session))
    engine.subscribe(writer)
    if backend is None:
        backend = make_backend(session)
    secure_off_mode = getattr(backend, "secure_off_mode", None)
    if secure_off_mode is not None:
        secure_off_mode(session.limits)
    flyer = SequenceFlyer(
        backend,
        name="k2460",
        poll_period_s=session.poll_period_s,
        start_timeout_s=session.start_timeout_s,
    )

    def run_from_sheet(
        run_sheet: str, measurement_id: str, sheet_sha256: str
    ) -> Generator[Msg, Any, str | None]:
        """Run one run-sheet row, validated against the session limits.

        The sheet must be byte-identical to the one enqueued.
        """
        request = load_request(session, run_sheet, measurement_id, sheet_sha256)
        md = {"run_sheet": str(run_sheet), "sheet_sha256": sheet_sha256}
        return (yield from run_sequence(flyer, request, md=md))

    def recover_sequencer() -> Generator[Msg, Any, None]:
        """After an abort or error: confirm output OFF and return the sequencer to IDLE."""
        yield from bps.null()
        flyer.recover().wait(timeout=60)

    def archive_retained_records() -> Generator[Msg, Any, str]:
        """After a host crash: save the 2460's unread records in the session data folder."""
        yield from bps.null()
        path = flyer.archive_retained(session.data_dir)
        print(f"Archived the retained records to {path}")
        return str(path)

    return {
        "RE": engine,
        "session": session,
        "run_file_writer": writer,
        "k2460": flyer,
        PLAN_NAME: run_from_sheet,
        "recover_sequencer": recover_sequencer,
        "archive_retained": archive_retained_records,
    }


def compile_for_2460(session: Session, request: SequenceRequest) -> Any:
    """Compile the request for the 2460 sequencer; raises if it cannot run there.

    Checks what session validation cannot: sampling feasibility, ranges and the
    63-trigger-block budget. Done for simulator sessions too, so a sheet that
    runs on the simulator also runs on the instrument.
    """
    from ..keithley.k2460.sequencer import compile_program

    return compile_program(
        request.sequence, session.limits, external_start=request.start_mode == "external"
    )


def queue_items(
    session: Session,
    run_sheet: str | Path,
    measurement_ids: list[str],
    *,
    show: Callable[[str], None] = print,
) -> list[dict[str, Any]]:
    """Validate and compile every selected row, show each summary, build the items.

    Nothing is enqueued unless every row is valid for the session and fits the
    2460 sequencer.
    """
    sheet = Path(run_sheet).resolve()
    digest = sheet_digest(sheet)
    runs = load_run_sheet(sheet, session.limits)
    missing = [m for m in measurement_ids if m not in runs]
    if missing:
        raise ValidationError(
            f"Not in the sheet: {', '.join(missing)}; available: {', '.join(runs)}"
        )
    items = []
    for measurement_id in measurement_ids:
        request = SequenceRequest.from_run_spec(runs[measurement_id], session.limits)
        try:
            compile_for_2460(session, request)
        except ValidationError as exc:
            raise ValidationError(f"{measurement_id}: {exc}; nothing was enqueued") from exc
        show(run_summary(request))
        items.append(
            {
                "name": PLAN_NAME,
                "kwargs": {
                    "run_sheet": str(sheet),
                    "measurement_id": measurement_id,
                    "sheet_sha256": digest,
                },
            }
        )
    return items


def enqueue(items: Iterable[Mapping[str, Any]], zmq_control_addr: str, *, api: Any = None) -> None:
    """Append the items to the queue, in order."""
    from bluesky_queueserver_api import BPlan  # type: ignore[import-untyped]
    from bluesky_queueserver_api.zmq import REManagerAPI  # type: ignore[import-untyped]

    manager = REManagerAPI(zmq_control_addr=zmq_control_addr) if api is None else api
    try:
        for item in items:
            manager.item_add(BPlan(item["name"], **item["kwargs"]))
    finally:
        if api is None:
            manager.close()


def validate_rows(
    session: Session, run_sheet: str | Path, measurement_ids: Iterable[str] = ()
) -> list[tuple[str, str | None, str]]:
    """``(measurement_id, problem or None, summary)`` per selected row (default all).

    Each row is checked against the session limits and compiled for the 2460
    sequencer (sampling feasibility, ranges, trigger-block budget). No
    instrument or RE Manager is contacted. A sheet-level error raises.
    """
    from .models import expected_records, nominal_duration_s

    runs = load_run_sheet(Path(run_sheet), session.limits)
    selected = list(measurement_ids) or list(runs)
    results: list[tuple[str, str | None, str]] = []
    for measurement_id in selected:
        if measurement_id not in runs:
            results.append((measurement_id, "not in the sheet", ""))
            continue
        try:
            request = SequenceRequest.from_run_spec(runs[measurement_id], session.limits)
            program = compile_for_2460(session, request)
        except ValidationError as exc:
            results.append((measurement_id, str(exc), ""))
            continue
        minutes, seconds = divmod(round(nominal_duration_s(request.sequence)), 60)
        nplc = "/".join(f"{m.nplc:g}" for m in program.measures)
        results.append(
            (
                measurement_id,
                None,
                f"{request.run_sheet_row.get('protocol', '')}, {request.start_mode} start, "
                f"{minutes // 60}:{minutes % 60:02d}:{seconds:02d}, "
                f"<= {expected_records(request.sequence)} records, "
                f"{len(program.blocks)} blocks, NPLC {nplc}",
            )
        )
    return results


def validate_main(argv: list[str] | None = None) -> int:
    """Check run-sheet rows against a session file without touching the instrument."""
    parser = argparse.ArgumentParser(prog="oe-validate", description=validate_main.__doc__)
    parser.add_argument("run_sheet", type=Path)
    parser.add_argument("measurement_ids", nargs="*", help="default: every row")
    parser.add_argument("--session", type=Path, required=True, help="the session TOML (OE_SESSION)")
    args = parser.parse_args(argv)
    try:
        session = load_session(args.session)
        results = validate_rows(session, args.run_sheet, args.measurement_ids)
    except ValidationError as exc:
        print(f"INVALID {args.run_sheet}: {exc}")
        return 1
    for measurement_id, problem, summary in results:
        print(f"{'FAIL' if problem else 'ok  '} {measurement_id}: {problem or summary}")
    failed = sum(problem is not None for _, problem, _ in results)
    print(f"{len(results) - failed} of {len(results)} run(s) valid for session {args.session}")
    return 1 if failed else 0


def archive_main(argv: list[str] | None = None) -> int:
    """Save the 2460's unread records of a crashed run (QueueServer environment closed)."""
    parser = argparse.ArgumentParser(prog="oe-archive-retained", description=archive_main.__doc__)
    parser.add_argument("--session", type=Path, required=True, help="the session TOML (OE_SESSION)")
    args = parser.parse_args(argv)
    session = load_session(args.session)
    path = archive_retained(make_backend(session), session.data_dir)
    print(f"Archived the retained records to {path}")
    return 0


def enqueue_main(argv: list[str] | None = None) -> int:
    """Validate run-sheet rows against the session and enqueue them into a running RE Manager."""
    parser = argparse.ArgumentParser(prog="oe-enqueue", description=enqueue_main.__doc__)
    parser.add_argument("run_sheet", type=Path)
    parser.add_argument("measurement_ids", nargs="+")
    parser.add_argument("--session", type=Path, required=True, help="the RE Manager's OE_SESSION")
    parser.add_argument("--zmq", default="tcp://127.0.0.1:60615")
    args = parser.parse_args(argv)
    session = load_session(args.session)
    if session.sample:
        print("Sample: " + ", ".join(f"{k} = {v}" for k, v in session.sample.items()))
    items = queue_items(session, args.run_sheet, args.measurement_ids)
    enqueue(items, args.zmq)
    print(f"Enqueued {len(items)} run(s): {', '.join(args.measurement_ids)}")
    return 0
