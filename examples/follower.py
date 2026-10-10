"""Plan template for a future contracted device; does not construct hardware.

Both modes: prepare leaves output OFF. External kickoff returns when READY;
the leader supplies START while complete waits on the finite program outcome.
"""

from pathlib import Path

from bluesky import plan_stubs as bps
from bluesky import preprocessors as bpp

from ophyd_electrochemistry import CurrentPulseSequence, StartMode
from ophyd_electrochemistry.exceptions import RetainedDataError, ValidationError


def follower_acquisition(
    device,
    request,
    *,
    md=None,
    archive_destination=None,
    disposition_reason="raw archive exported by follower plan",
):
    """Use the shared experiment ID in the follower's independent run."""

    run_md = dict(md or {})
    run_md.update({"experiment_id": request.experiment_id, "role": "follower"})

    def cleanup():
        device.stop(success=False)
        # Retained terminal buffers remain collectable after interruption.
        yield from bps.collect(device, return_payload=False)

    @bpp.stage_decorator([device])
    @bpp.run_decorator(md=run_md)
    def run():
        def body():
            # Suppress checkpoint replay of an armed/started acquisition.
            yield from bps.clear_checkpoint()
            yield from bps.prepare(device, request, wait=True)
            yield from bps.kickoff(device, wait=True)
            yield from bps.complete(device, wait=True)
            yield from bps.collect(device, return_payload=False)

        # stop after a normal COMPLETE is idempotent, preserving normal outcome.
        # On failed shutdown stop raises; do not imply guaranteed cleanup.
        yield from bpp.finalize_wrapper(body(), cleanup())

        if archive_destination is not None:
            acquisition_id = device.acquisition_id
            if acquisition_id is None:
                raise RetainedDataError("Cannot archive without an acquisition ID")
            device.export_retained_data(str(archive_destination))
            device.discard_retained_data(
                acquisition_id=acquisition_id,
                reason=disposition_reason,
            )

    return (yield from run())


def pulse_train_acquisition(device, request, **kwargs):
    """Run one locally timed pulse train after one immediate or external START."""

    if not isinstance(request.program, CurrentPulseSequence):
        raise ValidationError("pulse_train_acquisition requires CurrentPulseSequence")
    return (yield from follower_acquisition(device, request, **kwargs))


def repeated_single_pulse_acquisitions(device, request, archive_destinations):
    """Run separately armed external ``count=1`` shots with archival disposition."""

    if not isinstance(request.program, CurrentPulseSequence) or request.program.count != 1:
        raise ValidationError("Repeated shots require CurrentPulseSequence(count=1)")
    if request.start_mode != StartMode.EXTERNAL_TRIGGER:
        raise ValidationError("Repeated shots require external trigger mode")
    destinations = tuple(Path(destination) for destination in archive_destinations)
    if not destinations:
        raise ValidationError("Repeated shots require at least one archive destination")
    if len(set(destinations)) != len(destinations):
        raise ValidationError("Repeated-shot archive destinations must be unique")

    results = []
    for shot_index, destination in enumerate(destinations):
        result = yield from follower_acquisition(
            device,
            request,
            md={"shot_index": shot_index, "shot_count": len(destinations)},
            archive_destination=destination,
            disposition_reason=f"shot {shot_index} raw archive exported",
        )
        results.append(result)
    return tuple(results)
