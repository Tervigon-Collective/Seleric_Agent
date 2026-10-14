from seleric_swarm.forecasting.pipeline import _plain


def test_forecast_figures_never_use_exponent_notation():
    assert _plain(558579.3) == "558579"
    assert _plain(1545032.0) == "1545032"
    assert _plain(306.64) == "307"
    assert _plain(43.8) == "43.80"
    assert _plain(0.5) == "0.5"
    assert _plain(None) == "n/a"
    assert "e" not in _plain(2.5e-7)
