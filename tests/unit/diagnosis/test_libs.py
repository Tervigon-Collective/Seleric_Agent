"""Library adapters (plan P1.13): each does what its contract says on data with a known answer."""

from __future__ import annotations

import random

import numpy as np
import pytest

from seleric_swarm.diagnosis.domain.errors import LibraryFailed, LibraryUnavailable
from seleric_swarm.diagnosis.libs.changepoint import detect_changepoints
from seleric_swarm.diagnosis.libs.dowhy_estimate import estimate_backdoor_effect
from seleric_swarm.diagnosis.libs.dowhy_gcm import attribute_mean_change
from seleric_swarm.diagnosis.libs.hac import newey_west_lags, ols_hac
from seleric_swarm.diagnosis.libs.versions import NOT_INSTALLED, library_versions


def test_versions_name_every_tracked_library_and_say_when_one_is_missing():
    found = library_versions()
    assert {"dowhy", "statsmodels", "networkx", "numpy", "pandas"} <= set(found)
    assert all(v != NOT_INSTALLED for k, v in found.items() if k in {"dowhy", "statsmodels", "numpy"})
    assert library_versions(("surely-not-a-package",)) == {"surely-not-a-package": NOT_INSTALLED}


def test_ols_hac_recovers_a_known_slope_and_matches_statsmodels_directly():
    import statsmodels.api as sm

    rng = np.random.default_rng(3)
    n = 200
    x = rng.normal(size=n)
    noise = np.zeros(n)
    for i in range(1, n):  # serially correlated errors, like daily data
        noise[i] = 0.7 * noise[i - 1] + rng.normal(scale=0.5)
    y = 5 + 2 * x + noise
    fit = ols_hac(y, {"x": x})
    assert fit.params["x"] == pytest.approx(2.0, abs=0.15) and fit.n_rows == n
    assert fit.maxlags == newey_west_lags(n) == 4
    direct = sm.OLS(y, sm.add_constant(x)).fit(cov_type="HAC", cov_kwds={"maxlags": fit.maxlags})
    assert fit.stderr["x"] == pytest.approx(float(direct.bse[1])) and fit.pvalues["x"] < 1e-6


def test_ols_hac_refuses_a_design_it_cannot_fit():
    with pytest.raises(LibraryFailed):
        ols_hac([1.0, 2.0], {"x": [1.0, 2.0]})
    with pytest.raises(LibraryFailed):
        ols_hac([1.0, 2.0, 3.0], {"x": [1.0, 2.0]})


def test_backdoor_estimate_removes_confounding_that_a_naive_slope_keeps():
    rng = np.random.default_rng(5)
    n = 600
    z = rng.normal(size=n)
    t = 2 * z + rng.normal(size=n)
    y = 3 * t + 5 * z + rng.normal(size=n)
    naive = float(np.polyfit(t, y, 1)[0])
    estimate = estimate_backdoor_effect({"t": t, "y": y, "z": z}, treatment="t", outcome="y", adjusters=["z"])
    assert naive > 4.0  # biased upward by z
    assert estimate.effect == pytest.approx(3.0, abs=0.2)
    assert estimate.backdoor_set == ("z",) and estimate.n_rows == n
    with pytest.raises(LibraryFailed):
        estimate_backdoor_effect({"t": t, "y": y}, treatment="t", outcome="y", adjusters=["z"])


def _chain(rng: np.random.Generator, n: int, shift: float) -> dict[str, np.ndarray]:
    a = rng.normal(10, 1, n)
    b = 2 * a + rng.normal(0, 0.5, n) + shift
    return {"a": a, "b": b, "c": 3 * b + rng.normal(0, 0.5, n)}


def test_gcm_attributes_a_change_to_the_mechanism_that_moved_and_is_reproducible():
    rng = np.random.default_rng(0)
    old, new = _chain(rng, 100, 0.0), _chain(rng, 100, 4.0)
    edges = [("a", "b"), ("b", "c")]
    from dowhy import gcm

    np_before, py_before = np.random.get_state(), random.getstate()
    config_before = (gcm.config.default_n_jobs, gcm.config.show_progress_bars)
    first = attribute_mean_change(old, new, edges, "c", seed=11, num_samples=300)
    again = attribute_mean_change(old, new, edges, "c", seed=11, num_samples=300)
    assert first == again
    assert max(first, key=lambda k: abs(first[k])) == "b" and first["b"] == pytest.approx(12.0, abs=1.5)
    assert abs(first["a"]) < 1.0 and abs(first["c"]) < 1.0
    # global generators are exactly as they were: the adapter leaves the process undisturbed
    assert np.array_equal(np.random.get_state()[1], np_before[1]) and random.getstate() == py_before
    assert (gcm.config.default_n_jobs, gcm.config.show_progress_bars) == config_before
    with pytest.raises(LibraryFailed):
        attribute_mean_change(old, new, edges, "missing", seed=1)


def test_changepoints_find_a_level_shift_when_ruptures_is_installed():
    pytest.importorskip("ruptures", reason="ruptures is a Phase 4 dependency and is not in this image")
    rng = np.random.default_rng(1)
    series = np.concatenate([rng.normal(100, 2, 40), rng.normal(140, 2, 30)])
    assert detect_changepoints(series, penalty=50.0) == (40,)


def test_changepoints_say_so_when_the_library_is_missing_or_the_series_is_short():
    try:
        import ruptures  # noqa: F401
    except ImportError:
        with pytest.raises(LibraryUnavailable):
            detect_changepoints([1.0] * 30, penalty=5.0)
    else:
        with pytest.raises(LibraryFailed):
            detect_changepoints([1.0] * 5, penalty=5.0)
