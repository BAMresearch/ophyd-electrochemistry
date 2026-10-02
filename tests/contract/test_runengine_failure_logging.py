"""Expected failed completion must not leak a second exception at process exit."""

import os
import subprocess
import sys
from pathlib import Path


def test_aborted_follower_has_no_unretrieved_future_at_teardown():
    # A subprocess observes final garbage collection/process-exit logging, which
    # can occur after pytest reports a passing test. Run the real abort/retention
    # test unchanged: FailedStatus and a failed Stop document remain required.
    target = Path(__file__).with_name("test_bluesky_contract.py")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            f"{target}::test_documented_follower_plan_runs_cleanup_and_retains_partial_data[True]",
        ],
        env={**os.environ, "PYTHONASYNCIODEBUG": "1"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "Future exception was never retrieved" not in output, output
    assert "Task exception was never retrieved" not in output, output
