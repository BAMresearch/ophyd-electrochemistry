"""Plan template for a future contracted device; does not construct hardware.

Both modes: prepare leaves output OFF. External kickoff returns when READY;
the leader supplies START while complete waits on the finite program outcome.
"""

from bluesky import plan_stubs as bps
from bluesky import preprocessors as bpp


def follower_acquisition(device, request):
    """Use the shared experiment ID in the follower's independent run."""

    def cleanup():
        device.stop(success=False)
        # Retained terminal buffers remain collectable after interruption.
        yield from bps.collect(device, return_payload=False)

    @bpp.stage_decorator([device])
    @bpp.run_decorator(md={"experiment_id": request.experiment_id, "role": "follower"})
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

    return (yield from run())
