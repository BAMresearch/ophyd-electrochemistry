"""Keep the living user quickstart executable as the public contracts evolve."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from typing import Any

from ophyd_electrochemistry.state import DeviceState


def test_offline_quickstart_executes_and_ends_safe() -> None:
    root = Path(__file__).parents[2]
    path = root / "notebooks" / "ophyd_electrochemistry_quickstart.ipynb"
    notebook = json.loads(path.read_text(encoding="utf-8"))
    namespace: dict[str, Any] = {"__name__": "__main__"}

    with contextlib.redirect_stdout(io.StringIO()):
        for cell in notebook["cells"]:
            if cell["cell_type"] == "code":
                source = "".join(cell["source"])
                exec(compile(source, f"{path}:{cell['id']}", "exec"), namespace)

    rows = namespace["rows"]
    assert isinstance(rows, list) and len(rows) == 20
    assert all(
        "ec_voltage" in row
        and "ec_current" in row
        and "ec_source_status" in row
        and "ec_measurement_status" in row
        for row in rows
    )
    device = namespace["ec"]
    assert device.state == DeviceState.COMPLETE
    assert device.output_enabled is False
    assert isinstance(namespace["svg"], str) and namespace["svg"].startswith("<svg")


def test_guide_documents_separate_status_words() -> None:
    guide = (Path(__file__).parents[2] / "docs" / "user-guide.md").read_text(encoding="utf-8")
    assert "ec_source_status" in guide
    assert "ec_measurement_status" in guide
    assert "ec_status_bits" not in guide
