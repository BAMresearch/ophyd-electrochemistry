"""QueueServer startup for the K2460 sequencer: the session named by OE_SESSION.

See qserver/README.md. The worker that runs this file owns the instrument.
"""

import os

from ophyd_electrochemistry.sequences.queueserver import build_namespace, load_session

globals().update(build_namespace(load_session(os.environ["OE_SESSION"])))
