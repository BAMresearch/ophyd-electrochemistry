"""A real RE Manager on qserver/startup with the simulator session (needs redis-server)."""

import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("bluesky_queueserver")
from bluesky_queueserver_api import BPlan  # noqa: E402
from bluesky_queueserver_api.zmq import REManagerAPI  # noqa: E402

from ophyd_electrochemistry.sequences.queueserver import (  # noqa: E402
    enqueue,
    load_session,
    queue_items,
)
from ophyd_electrochemistry.sequences.run_file import read_run_file  # noqa: E402
from ophyd_electrochemistry.sequences.run_sheet import (  # noqa: E402
    make_template,
    write_run_sheet,
)

QSERVER = Path(__file__).parents[2] / "qserver"
REDIS = shutil.which("redis-server")
pytestmark = [
    pytest.mark.queueserver,
    pytest.mark.skipif(REDIS is None, reason="redis-server not installed"),
]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait(condition, timeout, what):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if condition():
                return
        except Exception:
            pass
        time.sleep(0.2)
    raise TimeoutError(what)


@pytest.fixture
def manager(tmp_path, request):
    speed = getattr(request, "param", "inf")  # simulator speed; 1.0 = real time
    redis_port, control, info = free_port(), free_port(), free_port()
    redis = subprocess.Popen(
        # Own --dir: redis loads dump.rdb from its working directory, and the
        # bench start script keeps one (the live queue) in the repository root.
        [
            REDIS,
            "--port",
            str(redis_port),
            "--save",
            "",
            "--appendonly",
            "no",
            "--dir",
            str(tmp_path),
        ],
        stdout=subprocess.DEVNULL,
    )
    session_file = tmp_path / "session.toml"
    session_file.write_text(
        (QSERVER / "session-simulator.toml")
        .read_text()
        .replace('"~/k2460-data/qserver-sim"', repr(str(tmp_path / "data")))
        .replace("sim_speed = 10.0", f"sim_speed = {speed}")
    )
    log = (tmp_path / "manager.log").open("w")
    process = subprocess.Popen(
        [
            str(Path(sys.executable).parent / "start-re-manager"),
            "--startup-dir",
            str(QSERVER / "startup"),
            "--existing-plans-devices",
            str(tmp_path / "existing_plans_and_devices.yaml"),
            "--update-existing-plans-devices",
            "ENVIRONMENT_OPEN",
            "--user-group-permissions",
            str(QSERVER / "startup" / "user_group_permissions.yaml"),
            "--redis-addr",
            f"localhost:{redis_port}",
            "--zmq-control-addr",
            f"tcp://127.0.0.1:{control}",
            "--zmq-info-addr",
            f"tcp://127.0.0.1:{info}",
        ],
        env={**os.environ, "OE_SESSION": str(session_file)},
        # Own process group: the manager and worker are spawned children that
        # outlive start-re-manager unless the whole group is stopped.
        start_new_session=True,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    api = REManagerAPI(zmq_control_addr=f"tcp://127.0.0.1:{control}", timeout_recv=5)
    try:
        wait(lambda: api.status(reload=True)["manager_state"] == "idle", 60, "RE Manager start")
        yield api, load_session(session_file), f"tcp://127.0.0.1:{control}"
    finally:
        try:
            if api.status(reload=True)["worker_environment_exists"]:
                api.environment_close()
                api.wait_for_idle(timeout=30)
            api.manager_stop()
            process.wait(timeout=30)
        except Exception:
            pass
        api.close()
        try:
            stop_group(process.pid)
        finally:
            redis.terminate()
            redis.wait(timeout=10)
            log.close()
        assert not group_alive(process.pid), "RE Manager processes survived the test"


def group_alive(pgid):
    # pgrep, not killpg(pgid, 0): macOS answers EPERM for a group of exited processes.
    return subprocess.run(["pgrep", "-g", str(pgid)], capture_output=True).returncode == 0


def stop_group(pgid):
    """Stop every process start-re-manager spawned, gracefully first."""
    for sig, grace in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):
        if not group_alive(pgid):
            return
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError):
            return
        deadline = time.monotonic() + grace
        while group_alive(pgid) and time.monotonic() < deadline:
            time.sleep(0.1)


def test_enqueued_row_runs_in_the_worker_and_writes_its_file(manager, tmp_path):
    api, session, address = manager
    api.environment_open()
    api.wait_for_idle(timeout=120)
    assert api.status()["worker_environment_exists"]
    allowed = api.plans_allowed()["plans_allowed"]
    assert set(allowed) == {"run_from_sheet", "recover_sequencer", "archive_retained"}
    with pytest.raises(Exception, match="count"):
        api.item_add(BPlan("count", ["k2460"]))
    sheet = make_template(tmp_path / "runs.xlsx")
    enqueue(queue_items(session, sheet, ["example-rest"], show=lambda _: None), address, api=api)
    api.queue_start()
    api.wait_for_idle(timeout=120)
    history = api.history_get()["items"]
    assert history[-1]["result"]["exit_status"] == "completed", history[-1]["result"]
    files = list((tmp_path / "data").rglob("*.h5"))
    assert len(files) == 1
    saved = read_run_file(files[0])
    assert saved["run"]["outcome"] == "completed"
    assert saved["run_sheet_row"]["measurement_id"] == "example-rest"


def test_a_relaxed_cutoff_lets_the_queue_run_the_next_row(manager, tmp_path):
    """Two-row sodiation end: the first row stops on rest_end_v, the second still runs."""
    api, session, address = manager
    api.environment_open()
    api.wait_for_idle(timeout=120)
    # Simulator cell: relaxed at about 0.1 V, so 0.2 V stops at the first check.
    row = dict(protocol="pulse_train", current_a=-1e-3, pulse_s=0.1, rest_s=3, pre_rest_s=1)
    sheet = write_run_sheet(
        tmp_path / "runs.xlsx",
        [
            dict(row, measurement_id="sod-early", repeats=5, rest_end_v=0.2),
            dict(row, measurement_id="sod-late", repeats=2, rest_end_v=0.05),
        ],
    )
    items = queue_items(session, sheet, ["sod-early", "sod-late"], show=lambda _: None)
    enqueue(items, address, api=api)
    api.queue_start()
    api.wait_for_idle(timeout=120)
    history = api.history_get()["items"]
    assert [h["result"]["exit_status"] for h in history[-2:]] == ["completed", "completed"]
    assert api.queue_get()["items"] == []
    outcomes = {}
    for path in (tmp_path / "data").rglob("*.h5"):
        saved = read_run_file(path)
        outcomes[saved["run_sheet_row"]["measurement_id"]] = saved["run"]["outcome"]
    assert outcomes == {"sod-early": "stopped_cutoff", "sod-late": "completed"}


@pytest.mark.parametrize("manager", ["1.0"], indirect=True)
def test_pause_immediate_abort_recover_and_run_again(manager, tmp_path):
    """The operator guide's stop procedure: pause immediate, abort, recover_sequencer."""
    api, session, address = manager
    api.environment_open()
    api.wait_for_idle(timeout=120)
    sheet = make_template(tmp_path / "runs.xlsx")  # example-rest lasts 10 s at speed 1
    items = queue_items(session, sheet, ["example-rest"], show=lambda _: None)
    enqueue(items, address, api=api)
    api.queue_start()
    wait(lambda: api.status(reload=True)["manager_state"] == "executing_queue", 30, "run start")
    time.sleep(2)
    api.re_pause(option="immediate")
    wait(lambda: api.status(reload=True)["manager_state"] == "paused", 30, "pause")
    api.re_abort()
    api.wait_for_idle(timeout=60)
    assert api.history_get()["items"][-1]["result"]["exit_status"] == "aborted"
    (aborted,) = (tmp_path / "data").rglob("*.h5")
    assert read_run_file(aborted)["run"]["outcome"] == "aborted"
    # QueueServer returns an aborted plan to the front of the queue: remove it,
    # recover first, then run the row again.
    queue = api.queue_get()["items"]
    assert [i["name"] for i in queue] == ["run_from_sheet"]
    api.item_remove(pos="front")
    api.item_add(BPlan("recover_sequencer"), pos="front")
    time.sleep(1.1)  # run files are named to the second
    enqueue(items, address, api=api)
    api.queue_start()
    api.wait_for_idle(timeout=120)
    results = [item["result"]["exit_status"] for item in api.history_get()["items"][-2:]]
    assert results == ["completed", "completed"], [
        i["result"].get("msg") for i in api.history_get()["items"]
    ]
    rerun = [f for f in (tmp_path / "data").rglob("*.h5") if f != aborted]
    assert read_run_file(rerun[0])["run"]["outcome"] == "completed"
