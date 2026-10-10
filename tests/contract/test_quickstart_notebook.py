"""Keep the living user quickstart executable as the public contracts evolve."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from typing import Any

import pytest
from plotly.graph_objects import Figure


def _execute_quickstart(
    monkeypatch: pytest.MonkeyPatch,
    *,
    replacements: dict[str, str] | None = None,
) -> tuple[Path, str, dict[str, Any]]:
    root = Path(__file__).parents[2]
    path = root / "notebooks" / "ophyd_electrochemistry_quickstart.ipynb"
    notebook_text = path.read_text(encoding="utf-8")
    notebook = json.loads(notebook_text)
    namespace: dict[str, Any] = {"__name__": "__main__"}

    monkeypatch.setattr(Figure, "show", lambda self: None)
    monkeypatch.chdir(path.parent)
    with contextlib.redirect_stdout(io.StringIO()):
        for cell in notebook["cells"]:
            if cell["cell_type"] == "code":
                source = "".join(cell["source"])
                for old, new in (replacements or {}).items():
                    source = source.replace(old, new)
                exec(compile(source, f"{path}:{cell['id']}", "exec"), namespace)
    return root, notebook_text, namespace


def test_offline_quickstart_executes_and_ends_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, notebook_text, namespace = _execute_quickstart(monkeypatch)

    assert "from examples" not in notebook_text
    rows = namespace["rows"]
    assert isinstance(rows, list) and len(rows) == 20
    assert all(
        "ec_voltage" in row
        and "ec_current" in row
        and "ec_source_status" in row
        and "ec_measurement_status" in row
        for row in rows
    )
    assert namespace["repo_root"] == root
    device = namespace["ec"]
    assert device.state.value == "complete"
    assert device.output_enabled is False
    figure = namespace["figure"]
    assert isinstance(figure, Figure)
    assert len(figure.data) == 3
    assert [trace.name for trace in figure.data] == [
        "Voltage",
        "Measured current",
        "Commanded current",
    ]
    assert set(namespace["program_examples"]) == {
        "current_pulse",
        "voltage_pulse",
        "alternating_current",
    }


@pytest.mark.parametrize(
    ("selected_program", "source_function", "command_trace"),
    [
        ("voltage_pulse", "voltage", "Commanded voltage"),
        ("alternating_current", "current", "Commanded current"),
    ],
)
def test_quickstart_alternative_pulse_programs_execute(
    monkeypatch: pytest.MonkeyPatch,
    selected_program: str,
    source_function: str,
    command_trace: str,
) -> None:
    _, _, namespace = _execute_quickstart(
        monkeypatch,
        replacements={
            'selected_program = "current_pulse"': f'selected_program = "{selected_program}"'
        },
    )

    assert namespace["compiled"].source_function == source_function
    assert namespace["ec"].state.value == "complete"
    assert namespace["ec"].output_enabled is False
    assert namespace["figure"].data[2].name == command_trace
    if selected_program == "alternating_current":
        assert namespace["compiled"].commanded_charge_c == pytest.approx(0)


def test_quickstart_derives_runtime_bounds_for_longer_program(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, namespace = _execute_quickstart(
        monkeypatch,
        replacements={
            "pulse_width_s=0.020": "pulse_width_s=0.200",
            "period_s=0.050": "period_s=0.500",
            "command_timeout_s=1.0": "command_timeout_s=10.0",
            "shutdown_timeout_s=1.0": "shutdown_timeout_s=10.0",
        },
    )

    compiled = namespace["compiled"]
    assert compiled.achieved_duration_s == 2.0
    assert namespace["simulation_advance_ticks"] == compiled.source.duration_ticks + 1
    assert namespace["host_completion_timeout_s"] >= 12.0
    assert len(namespace["rows"]) == 200
    assert namespace["ec"].state.value == "complete"
    assert namespace["ec"].output_enabled is False


def test_guide_documents_separate_status_words() -> None:
    guide = (Path(__file__).parents[2] / "docs" / "user-guide.md").read_text(encoding="utf-8")
    assert "ec_source_status" in guide
    assert "ec_measurement_status" in guide
    assert "ec_status_bits" not in guide
