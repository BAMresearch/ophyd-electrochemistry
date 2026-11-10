"""The B3 sheets (healthy cell: overnight P0, draft beamtime pulses) validate offline."""

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("openpyxl")

BENCH = Path(__file__).parents[2] / "bench"


@pytest.fixture(scope="module")
def sheets():
    spec = importlib.util.spec_from_file_location("b3_sheets", BENCH / "b3_sheets.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "options",
    [
        [],
        ["--capacity-mah", "0.53", "--rate-c", "40", "--external"],
        ["--capacity-mah", "1", "--rate-c", "5"],
    ],
)
def test_every_row_validates_against_its_session(sheets, tmp_path, capsys, options):
    assert sheets.main([*options, "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "FAIL" not in out
    assert sorted(p.name for p in tmp_path.glob("*.xlsx")) == sorted(sheets.SHEETS)


def test_p0_is_c10_to_5_mv_inside_a_narrow_session(sheets):
    rows = {row["measurement_id"]: row for row in sheets.p0_rows(0.6, "cell")}
    assert rows["hc-p0"]["current_a"] == pytest.approx(-60e-6)
    assert rows["hc-p0"]["target_v"] == 0.005
    assert rows["hc-p0-cv"]["cv_i_abs_min_a"] == pytest.approx(12e-6)
    from ophyd_electrochemistry.sequences.queueserver import load_session

    limits = load_session(BENCH / "b3" / "session-b3-p0.toml").limits
    assert (limits.v_min_abs, limits.v_max_abs, limits.i_max_abs) == (0.0, 3.0, 1e-4)


def test_pulse_rows_end_on_the_relaxed_voltage(sheets):
    rows = sheets.pulse_rows(0.6, "cell", 10, desod_end_v=0.2, sod_early_v=0.05, external=True)
    ends = {row["measurement_id"]: row.get("rest_end_v") for row in rows}
    assert ends == {"p-ocv": None, "p2d-desod": 0.2, "p2s-sod-1min": 0.05, "p2s-sod-5min": 0.005}
    assert all(row["start_mode"] == "external" for row in rows[1:])
    assert rows[1]["current_a"] == pytest.approx(6e-3) and rows[2]["current_a"] < 0


def test_currents_beyond_a_session_are_refused_before_writing(sheets, tmp_path, capsys):
    with pytest.raises(SystemExit):
        sheets.main(["--capacity-mah", "1", "--rate-c", "40", "--out", str(tmp_path)])
    assert "40 mA exceeds its session's 30 mA" in capsys.readouterr().err
    assert not list(tmp_path.glob("*.xlsx"))


def test_existing_sheets_are_not_overwritten(sheets, tmp_path):
    assert sheets.main(["--out", str(tmp_path)]) == 0
    with pytest.raises(Exception, match="exists"):
        sheets.main(["--out", str(tmp_path)])
