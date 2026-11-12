"""The B3 analysis helpers recover R, k and the quasi-OCV of a synthetic ICI trace."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest

BENCH = Path(__file__).parents[2] / "bench"


@pytest.fixture(scope="module")
def analysis():
    spec = importlib.util.spec_from_file_location("b3_analysis", BENCH / "b3_analysis.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


P = 1666 * 0.006  # about 10 s: the last reading of a step falls on the switch


def synthetic_ici(current=130e-6, r=100.0, k=60.0, steps=3):
    """~10 s current steps and pauses at 6 ms; OCV rises 1 mV per step.

    Under load V = OCV + I R + I k sqrt(t_on); in a pause the k part decays as
    -I k sqrt(t_off) (the ICI model for t_off << t_on)."""
    t, v, i, ocv, now = [], [], [], 0.5, 0.0
    for _ in range(steps):
        for dt in 0.006 * np.arange(1, 1667):
            t.append(now + dt)
            v.append(ocv + current * (r + k * np.sqrt(dt)))
            i.append(current)
        now += P
        for dt in 0.006 * np.arange(1, 1667):
            t.append(now + dt)
            v.append(ocv + current * k * (np.sqrt(P) - np.sqrt(dt)))
            i.append(0.0)
        now += P
        ocv += 0.001
    return np.array(t), np.array(v), np.array(i)


def test_ici_pauses_recover_r_k_and_the_pause_end(analysis):
    t, v, i = synthetic_ici()
    rows = analysis.ici_pauses(t, v, i)
    assert len(rows) == 3
    q, v_load, r, k, v_end, r0 = rows.T
    # R_first from one 6 ms sample: the k part adds I k sqrt(0.006) to the step;
    # R0 from the sqrt(t) intercept is the model's R (brief 14)
    assert r == pytest.approx(100 + 60 * np.sqrt(0.006), rel=1e-3)
    assert r0 == pytest.approx(100, rel=1e-6)
    assert k == pytest.approx(60, rel=1e-6)
    assert np.diff(v_end) == pytest.approx(0.001, abs=1e-9)
    assert np.diff(q) == pytest.approx(130e-6 * P / 3.6e-3, rel=1e-2)


def test_charge_and_overpotential_helpers(analysis):
    t, v, i = synthetic_ici(steps=2)
    assert analysis.charge_uah(t, i)[-1] == pytest.approx(2 * 130e-6 * P / 3.6e-3, rel=1e-2)
    current, eta = analysis.eta_at(t, v, i, 0.5)
    assert current == pytest.approx(130e-6)
    # the second pulse starts from the end of the first pause, not from 0.5 V
    assert eta == pytest.approx(130e-6 * (100 + 60 * np.sqrt(0.5)) - 0.0, abs=2e-3)
    assert analysis.butler_volmer(0.0, 30, 0.1, 1e-5) == 0.0


def test_no_interruptions_gives_an_empty_table(analysis):
    t = np.arange(0, 10, 0.1)
    assert analysis.ici_pauses(t, 0.5 + 0 * t, 1e-4 + 0 * t).shape == (0, 6)
    # a sole pulse already on at the first reading has no baseline (brief 14)
    with pytest.raises(ValueError, match="baseline"):
        analysis.eta_at(t, 0.5 + 0 * t, 1e-4 + 0 * t, 0.5)


def test_npz_archives_need_the_current_mode_confirmation(analysis, tmp_path):
    path = tmp_path / "retained.npz"
    seconds = np.array([100, 100, 101])
    np.savez(
        path,
        seconds=seconds,
        fraction=np.array([0.0, 0.5, 0.0]),
        reading=np.array([0.5, 0.6, 0.7]),
        source=np.array([0.0, 1e-4, 1e-4]),
    )
    with pytest.raises(ValueError, match="npz-current-mode"):
        analysis.load(path)
    t, v, i = analysis.load(path, npz_current_mode=True)
    assert list(t) == [0.0, 0.5, 1.0] and v[-1] == 0.7 and i[-1] == 1e-4
