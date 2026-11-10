"""Bluesky sequence plan; validation and loading precede the RunStart document."""

# mypy: disable-error-code="no-untyped-call"

import logging
from collections.abc import Generator, Mapping
from typing import Any

from bluesky import plan_stubs as bps
from bluesky import preprocessors as bpp
from bluesky.utils import Msg

from ..exceptions import ValidationError
from ..validation import positive
from .backend import SequenceRequest
from .flyer import SequenceFlyer
from .models import expected_records, nominal_duration_s, to_dict

logger = logging.getLogger(__name__)


def run_sequence(
    flyer: SequenceFlyer,
    request: SequenceRequest,
    *,
    md: Mapping[str, Any] | None = None,
    flush_period_s: float = 1.0,
) -> Generator[Msg, Any, str | None]:
    """Stage, prepare, run with live collection, drain on interruption, unstage."""
    positive(flush_period_s, "flush_period_s")
    metadata = {
        "measurement_id": request.measurement_id,
        "sample_id": request.sample_id,
        "notes": request.notes,
        "start_mode": request.start_mode,
        "sequence": to_dict(request.sequence),
        "safety_limits": to_dict(request.limits),
        "run_sheet_row": dict(request.run_sheet_row),
        "equipment": {},
        "expected_records": expected_records(request.sequence),
        "nominal_duration_s": nominal_duration_s(request.sequence),
        "plan_name": "run_sequence",
    }
    collisions = metadata.keys() & (md or {}).keys()
    if collisions:
        raise ValidationError(f"Reserved run metadata: {', '.join(sorted(collisions))}")
    metadata.update(md or {})
    original_error = False
    kicked = False

    def body() -> Generator[Msg, Any, str | None]:
        nonlocal original_error, kicked
        try:
            kicked = True
            yield from bps.kickoff(flyer, wait=True)
            yield from bps.collect_while_completing(
                [flyer],
                [flyer],
                flush_period=flush_period_s,
            )
            return flyer.outcome
        except BaseException:
            original_error = True
            raise

    def cleanup() -> Generator[Msg, Any, None]:
        errors = []
        try:
            yield from bps.stop(flyer)
        except Exception as exc:
            errors.append(exc)
        if kicked:
            try:
                yield from bps.collect(flyer, return_payload=False)
            except Exception as exc:
                errors.append(exc)
        if errors:
            if original_error:
                logger.error(
                    "Sequence cleanup failed while handling the original error: %s", errors
                )
            else:
                raise errors[0]

    def staged() -> Generator[Msg, Any, str | None]:
        # bps.prepare waits for the returned Status before opening a run.
        yield from bps.prepare(flyer, request, wait=True)
        metadata["equipment"] = flyer.equipment
        yield from bpp.run_wrapper(bpp.finalize_wrapper(body(), cleanup()), md=metadata)
        return flyer.outcome

    yield from bpp.stage_wrapper(staged(), [flyer])
    return flyer.outcome
