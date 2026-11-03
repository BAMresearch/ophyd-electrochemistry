"""Execute every viewer cell against saved simulator runs, with no acquisition."""

import hashlib
from pathlib import Path

import nbformat
import pandas as pd
import pytest
from plotly.graph_objects import Figure

TEMPLATE = Path(__file__).parents[2] / "notebooks" / "k2460_view.ipynb"


def execute(monkeypatch, directory, *, follow=None, compare=None, axis="charge"):
    figures, displays, updates = [], [], []
    monkeypatch.setattr(Figure, "show", lambda self: figures.append(self))

    class Handle:
        def update(self, obj, **kwargs):
            updates.append((obj, kwargs))

    def display(obj, **kwargs):
        displays.append((obj, kwargs))
        return Handle()

    monkeypatch.setattr("IPython.display.display", display)
    namespace = {}
    replacements = {
        'DATA_DIR = Path.home() / "k2460-data"': f"DATA_DIR = Path({str(directory)!r})",
        "FOLLOW = None": f"FOLLOW = {follow!r}",
        "COMPARE = []": f"COMPARE = {compare or []!r}",
        'X_AXIS = "charge"': f"X_AXIS = {axis!r}",
    }
    for cell in nbformat.read(TEMPLATE, as_version=4).cells:
        if cell.cell_type != "code":
            continue
        source = cell.source
        for before, after in replacements.items():
            source = source.replace(before, after)
        exec(compile(source, f"{TEMPLATE}:{cell.id}", "exec"), namespace)
    return namespace, figures, displays, updates


@pytest.mark.parametrize("axis", ["time", "charge"])
def test_viewer_notebook_end_to_end(monkeypatch, viewer_runs, axis):
    directory, paths = viewer_runs
    before = {path: hashlib.sha256(path.read_bytes()).digest() for path in paths.values()}
    namespace, figures, displays, updates = execute(
        monkeypatch,
        directory,
        follow=paths["completed"].name,
        axis=axis,
    )
    assert namespace["outcome"] == "completed"
    assert namespace["index"].outcome.tolist() == list(reversed(paths))
    assert figures == [namespace["full_figure"], namespace["comparison"]]
    assert len(figures[0].data[0].x) == len(namespace["saved"]["readout"]["t_s"])
    assert namespace["compare_paths"] == [paths["aborted"], paths["stopped_cutoff"]]
    assert len(figures[1].data) == 2
    assert [type(obj) for obj, _ in displays] == [pd.DataFrame, dict, pd.DataFrame]
    assert displays[1][1] == {"raw": True, "display_id": True}
    assert "application/vnd.plotly.v1+json" in displays[1][0]
    assert not updates  # FOLLOW is already finished.
    assert before == {path: hashlib.sha256(path.read_bytes()).digest() for path in paths.values()}


def test_viewer_explicit_comparison_and_latest(monkeypatch, viewer_runs, tmp_path):
    _, paths = viewer_runs
    # A directory whose newest file is finished allows default Follow to return.
    selected = tmp_path / paths["completed"].name
    selected.write_bytes(paths["completed"].read_bytes())
    namespace, figures, _, _ = execute(monkeypatch, tmp_path, compare=[selected.name])
    assert namespace["selected_path"] == selected
    assert namespace["compare_paths"] == [selected]
    assert len(figures[1].data) == 1


def test_viewer_empty_directory(monkeypatch, tmp_path, capsys):
    namespace, figures, displays, updates = execute(monkeypatch, tmp_path)
    assert namespace["index"].empty
    assert namespace["selected_path"] is None
    assert not figures and not updates
    assert len(displays) == 1
    assert "No HDF5 runs found" in capsys.readouterr().out
    assert not list(tmp_path.iterdir())


def test_template_is_clean_and_has_one_settings_cell():
    notebook = nbformat.read(TEMPLATE, as_version=4)
    nbformat.validate(notebook)
    settings = [cell for cell in notebook.cells if "settings" in cell.metadata.get("tags", [])]
    assert len(settings) == 1 and settings[0].cell_type == "code"
    for name in ("DATA_DIR", "FOLLOW", "COMPARE", "X_AXIS"):
        assert f"{name} = " in settings[0].source
    for cell in notebook.cells:
        if cell.cell_type == "code":
            assert not cell.outputs
            assert cell.execution_count is None
