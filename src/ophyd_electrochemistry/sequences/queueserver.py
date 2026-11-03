# mypy: disable-error-code="no-untyped-call"

"""QueueServer integration: session file, startup namespace and the enqueue command.

The RE Manager worker is the one process that owns the instrument. A session
TOML file (``OE_SESSION``) fixes the backend, data directory and safety limits
for every queued run. A queue item names one run-sheet row and the sheet's
SHA-256 at enqueue time; the plan refuses a sheet that changed since. For a
hardware session the operator confirms every run when enqueuing it, by typing
its measurement ID after reviewing its summary. Requires the ``experiment`` and
``queueserver`` extras (and ``visa`` for the 2460).
"""

import argparse
import hashlib
import tomllib
from collections.abc import Callable, Generator, Iterable, Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Literal

from bluesky import RunEngine
from bluesky import plan_stubs as bps
from bluesky.utils import Msg

from ..exceptions import ValidationError
from ..validation import positive, text
from .backend import SequenceBackend, SequenceRequest, SimulatedSequenceBackend
from .callbacks import RunFileWriter
from .flyer import SequenceFlyer
from .models import SafetyLimits
from .notebook import require_confirmation, run_summary
from .plans import run_sequence
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

    @property
    def hardware(self) -> bool:
        return self.backend != "simulator"


def load_session(path: str | Path) -> Session:
    """Read a session TOML file: top-level settings and a ``[limits]`` table."""
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    limits = data.pop("limits", None)
    if not isinstance(limits, dict):
        raise ValidationError("session file needs a [limits] table")
    known = {f.name for f in fields(Session)} - {"limits"}
    unknown = data.keys() - known
    if unknown:
        raise ValidationError(f"unknown session settings: {', '.join(sorted(unknown))}")
    if "data_dir" not in data:
        raise ValidationError("session file needs data_dir")
    data["data_dir"] = Path(data["data_dir"]).expanduser()
    try:
        return Session(limits=SafetyLimits(**limits), **data)
    except TypeError as exc:
        raise ValidationError(f"invalid session file: {exc}") from exc


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
    """RunEngine, run-file writer, Flyer and the queueable plans for a startup file."""
    engine = RunEngine({})
    writer = RunFileWriter(session.data_dir)
    engine.subscribe(writer)
    flyer = SequenceFlyer(
        make_backend(session) if backend is None else backend,
        name="k2460",
        poll_period_s=session.poll_period_s,
        start_timeout_s=session.start_timeout_s,
    )

    def run_from_sheet(
        run_sheet: str, measurement_id: str, sheet_sha256: str, confirm_measurement_id: str = ""
    ) -> Generator[Msg, Any, str | None]:
        """Run one run-sheet row, validated against the session limits.

        The sheet must be byte-identical to the one enqueued. Hardware sessions
        need confirm_measurement_id equal to measurement_id (the enqueue command
        asks the operator for it).
        """
        request = load_request(session, run_sheet, measurement_id, sheet_sha256)
        require_confirmation(request, confirm_measurement_id, hardware=session.hardware)
        return (yield from run_sequence(flyer, request))

    def recover_sequencer() -> Generator[Msg, Any, None]:
        """After an abort or error: confirm output OFF and return the sequencer to IDLE."""
        yield from bps.null()
        flyer.recover().wait(timeout=60)

    return {
        "RE": engine,
        "session": session,
        "run_file_writer": writer,
        "k2460": flyer,
        PLAN_NAME: run_from_sheet,
        "recover_sequencer": recover_sequencer,
    }


def queue_items(
    session: Session,
    run_sheet: str | Path,
    measurement_ids: list[str],
    *,
    prompt: Callable[[str], str] = input,
    show: Callable[[str], None] = print,
) -> list[dict[str, Any]]:
    """Validate the sheet, review every run and collect the operator's confirmations."""
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
        show(run_summary(request))
        confirmed = ""
        if session.hardware:
            confirmed = prompt(
                f"Check the cell, wiring, voltage window, current limit and output-off mode "
                f"HIGH-Z for this run, then type {measurement_id!r} to confirm: "
            ).strip()
            if confirmed != measurement_id:
                raise ValidationError(f"{measurement_id} not confirmed; nothing was enqueued")
        items.append(
            {
                "name": PLAN_NAME,
                "kwargs": {
                    "run_sheet": str(sheet),
                    "measurement_id": measurement_id,
                    "sheet_sha256": digest,
                    "confirm_measurement_id": confirmed,
                },
            }
        )
    return items


def enqueue(items: Iterable[Mapping[str, Any]], zmq_control_addr: str, *, api: Any = None) -> None:
    """Append the items to the queue, in order (only after every run was confirmed)."""
    from bluesky_queueserver_api import BPlan  # type: ignore[import-untyped]
    from bluesky_queueserver_api.zmq import REManagerAPI  # type: ignore[import-untyped]

    manager = REManagerAPI(zmq_control_addr=zmq_control_addr) if api is None else api
    try:
        for item in items:
            manager.item_add(BPlan(item["name"], **item["kwargs"]))
    finally:
        if api is None:
            manager.close()


def main(argv: list[str] | None = None) -> int:
    """Enqueue run-sheet rows into a running RE Manager (one confirmation per hardware run)."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("run_sheet", type=Path)
    parser.add_argument("measurement_ids", nargs="+")
    parser.add_argument("--session", type=Path, required=True, help="the RE Manager's OE_SESSION")
    parser.add_argument("--zmq", default="tcp://127.0.0.1:60615")
    args = parser.parse_args(argv)
    session = load_session(args.session)
    items = queue_items(session, args.run_sheet, args.measurement_ids)
    enqueue(items, args.zmq)
    print(f"Enqueued {len(items)} run(s): {', '.join(args.measurement_ids)}")
    return 0
