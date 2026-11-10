"""Notebook copies validate the real session bounds and preserve existing records."""

from dataclasses import replace
from pathlib import Path

import nbformat
import numpy as np
import pandas as pd
import pytest
from plotly.graph_objects import Figure

from ophyd_electrochemistry.exceptions import ValidationError
from ophyd_electrochemistry.sequences import SafetyLimits, SequenceRequest
from ophyd_electrochemistry.sequences.models import expected_records
from ophyd_electrochemistry.sequences.notebook import (
    main,
    new_run_notebook,
    preview_figure,
    run_summary,
)
from ophyd_electrochemistry.sequences.run_sheet import load_run_sheet, make_template
from ophyd_electrochemistry.sequences.simulator import CellModel, simulate_sequence

TEMPLATE = Path(__file__).parents[2] / "notebooks" / "k2460_run.ipynb"


@pytest.fixture
def limits():
    return SafetyLimits(0, 3, 0.01, 200000, 1000000)


@pytest.fixture
def sheet(tmp_path):
    return make_template(tmp_path / "runs.xlsx")


@pytest.fixture
def request_spec(sheet, limits):
    return SequenceRequest.from_run_spec(load_run_sheet(sheet, limits)["example-rest"], limits)


def test_summary_includes_bounds_and_full_row(request_spec):
    request = replace(request_spec, sample_id="cell-01", notes="operator note")
    summary = run_summary(request)
    for value in (
        "example-rest",
        "protocol: rest",
        "cell-01",
        "operator note",
        "immediate",
        "expanded steps: 1",
        "0:00:10",
        str(expected_records(request.sequence)),
        "v_min_abs: 0",
        "v_max_abs: 3",
        "i_max_abs: 0.01",
        "max_duration_s: 200000",
        "max_records: 1000000",
        "min_interval_s: 0.005",
    ):
        assert value in summary
    for key, value in request.run_sheet_row.items():
        assert f"{key}: {value}" in summary


def test_preview_is_the_seeded_simulation(request_spec):
    cell = CellModel(noise_v=0.001)
    figure = preview_figure(request_spec, cell, seed=17)
    result = simulate_sequence(request_spec.sequence, request_spec.limits, cell, seed=17)
    assert isinstance(figure, Figure)
    assert figure.layout.title.text == "Preview — simulated cell, not a prediction"
    np.testing.assert_array_equal(figure.data[0].x, result.readout["t_s"])
    np.testing.assert_array_equal(figure.data[0].y, result.readout["voltage_v"])
    np.testing.assert_array_equal(figure.data[1].y, result.readout["current_a"])


def test_copy_fills_settings_clears_outputs_and_refuses_overwrite(tmp_path, sheet, limits):
    original = nbformat.read(TEMPLATE, as_version=4)
    for cell in original.cells:
        if cell.cell_type == "code":
            cell.execution_count = 4
            cell.outputs = [nbformat.v4.new_output("stream", name="stdout", text="old run")]
    dirty = tmp_path / "dirty.ipynb"
    nbformat.write(original, dirty)
    path = new_run_notebook(sheet, "example-rest", tmp_path / "copies", limits, template=dirty)
    copied = nbformat.read(path, as_version=4)
    settings = next(cell for cell in copied.cells if "settings" in cell.metadata.get("tags", []))
    namespace = {}
    exec(settings.source, namespace)
    assert namespace["MEASUREMENT_ID"] == "example-rest"
    assert namespace["RUN_SHEET"] == sheet.resolve()
    assert all(
        not cell.outputs and cell.execution_count is None
        for cell in copied.cells
        if cell.cell_type == "code"
    )
    assert nbformat.read(dirty, as_version=4) == original
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        new_run_notebook(sheet, "example-rest", path.parent, limits)
    assert path.read_bytes() == before


def test_copy_runs_with_the_validated_limits_beside_its_file(tmp_path, sheet):
    # Tighter than the template's placeholders: the copy must not fall back to them.
    session = SafetyLimits(0.01, 2.5, 0.002, 7200, 50000, min_interval_s=0.004)
    path = new_run_notebook(sheet, "example-rest", tmp_path / "run dir", session)
    settings = next(
        cell
        for cell in nbformat.read(path, as_version=4).cells
        if "settings" in cell.metadata.get("tags", [])
    )
    namespace = {}
    exec(settings.source, namespace)
    assert namespace["LIMITS"] == session
    assert namespace["DATA_DIR"] == path.parent == (tmp_path / "run dir").resolve()
    assert "DUMMY-CELL" not in settings.source
    # Settings the generator does not own are kept.
    assert namespace["BACKEND"] == "simulator"


def test_copy_validates_before_writing(tmp_path, sheet, limits):
    destination = tmp_path / "copies"
    with pytest.raises(ValidationError, match="available IDs:.*example-rest"):
        new_run_notebook(sheet, "unknown", destination, limits)
    with pytest.raises(ValidationError, match="max_duration_s"):
        new_run_notebook(sheet, "example-rest", destination, replace(limits, max_duration_s=1))
    assert not destination.exists()


def test_copy_sanitizes_id_and_quotes_values(tmp_path, limits):
    measurement_id = '../cell "α" / 01'
    sheet = tmp_path / "run 'sheet.xlsx"
    pd.DataFrame([dict(measurement_id=measurement_id, protocol="rest", rest_s=1)]).to_excel(
        sheet, index=False
    )
    path = new_run_notebook(sheet, measurement_id, tmp_path / "copies", limits)
    assert path.name == ".._cell_______01.ipynb"
    notebook = nbformat.read(path, as_version=4)
    settings = next(c for c in notebook.cells if "settings" in c.metadata.get("tags", []))
    namespace = {}
    exec(settings.source, namespace)
    assert namespace["MEASUREMENT_ID"] == measurement_id
    assert namespace["RUN_SHEET"] == sheet


@pytest.mark.parametrize("broken", ["tag", "duplicate", "MEASUREMENT_ID =", "RUN_SHEET ="])
def test_malformed_template_fails_loudly(tmp_path, sheet, limits, broken):
    notebook = nbformat.read(TEMPLATE, as_version=4)
    settings = next(c for c in notebook.cells if "settings" in c.metadata.get("tags", []))
    if broken == "tag":
        settings.metadata.tags = []
    elif broken == "duplicate":
        notebook.cells.append(nbformat.v4.new_code_cell("", metadata={"tags": ["settings"]}))
    else:
        settings.source = "\n".join(
            line for line in settings.source.splitlines() if not line.startswith(broken)
        )
    template = tmp_path / "broken.ipynb"
    nbformat.write(notebook, template)
    with pytest.raises(ValidationError, match="[Ss]ettings"):
        new_run_notebook(sheet, "example-rest", tmp_path / "copies", limits, template=template)
    assert not (tmp_path / "copies").exists()


def test_cli_roundtrip(tmp_path, sheet, capsys):
    destination = tmp_path / "cli"
    assert (
        main(
            [
                str(sheet),
                "example-rest",
                str(destination),
                "--v-min",
                "0",
                "--v-max",
                "3",
                "--i-max",
                "0.01",
                "--max-duration-s",
                "200000",
                "--max-records",
                "1000000",
            ]
        )
        == 0
    )
    path = Path(capsys.readouterr().out.strip())
    assert path == destination / "example-rest.ipynb"
    nbformat.validate(nbformat.read(path, as_version=4))


def test_cli_requires_explicit_limits(sheet, tmp_path):
    with pytest.raises(SystemExit) as error:
        main([str(sheet), "example-rest", str(tmp_path)])
    assert error.value.code == 2


def _device_namespace(backend_name, request):
    cells = nbformat.read(TEMPLATE, as_version=4).cells
    settings = next(c for c in cells if "settings" in c.metadata.get("tags", []))
    device = next(c for c in cells if "k2460_sequence_backend(" in c.source)
    namespace = {}
    exec(settings.source, namespace)
    namespace.update(BACKEND=backend_name, request=request)
    exec(device.source, namespace)
    return namespace


def test_template_builds_the_2460_backend_without_touching_it(request_spec):
    from ophyd_electrochemistry.keithley.k2460.sequence_backend import (
        Keithley2460SequenceBackend,
    )

    namespace = _device_namespace("k2460", request_spec)
    backend = namespace["backend"]
    assert isinstance(backend, Keithley2460SequenceBackend)
    assert backend.terminals == "front" and backend.four_wire
    transport = backend._transport
    assert not transport.connected
    assert transport.config.resource_name == namespace["VISA_RESOURCE"]
    assert transport.config.required_language == "TSP"
    assert namespace["flyer"].name == "k2460"


def test_template_rejects_unknown_backend(request_spec):
    with pytest.raises(ValidationError, match="Unknown BACKEND"):
        _device_namespace("keithley", request_spec)
