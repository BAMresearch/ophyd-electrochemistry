"""A real RE Manager on qserver/startup with the simulator session (needs redis-server)."""

import shutil
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
from ophyd_electrochemistry.sequences.run_sheet import make_template  # noqa: E402

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
def manager(tmp_path):
    redis_port, control, info = free_port(), free_port(), free_port()
    redis = subprocess.Popen(
        [REDIS, "--port", str(redis_port), "--save", "", "--appendonly", "no"],
        stdout=subprocess.DEVNULL,
    )
    session_file = tmp_path / "session.toml"
    session_file.write_text(
        (QSERVER / "session-simulator.toml")
        .read_text()
        .replace('"~/k2460-data/qserver-sim"', repr(str(tmp_path / "data")))
        .replace("sim_speed = 10.0", "sim_speed = inf")
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
        env={**__import__("os").environ, "OE_SESSION": str(session_file)},
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
            process.kill()
        api.close()
        redis.terminate()
        redis.wait(timeout=10)
        log.close()


def test_enqueued_row_runs_in_the_worker_and_writes_its_file(manager, tmp_path):
    api, session, address = manager
    api.environment_open()
    api.wait_for_idle(timeout=120)
    assert api.status()["worker_environment_exists"]
    allowed = api.plans_allowed()["plans_allowed"]
    assert set(allowed) == {"run_from_sheet", "recover_sequencer"}
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
