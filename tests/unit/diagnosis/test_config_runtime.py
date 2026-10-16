"""Config and runtime (plan P1.11): typed, versioned, equal to today's constants, deterministic streams."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import numpy as np
import pytest

from seleric_swarm.diagnosis.adapters.runtime import SeededRuntime
from seleric_swarm.diagnosis.domain.config import Bounds, DiagnosisConfig, InvalidConfig
from seleric_swarm.toolsets import policy_config


def _legacy() -> dict[str, Any]:
    return {n[len("DIAG_"):].lower(): getattr(policy_config, n) for n in dir(policy_config) if n.startswith("DIAG_")}


def test_defaults_equal_the_policy_constants_and_cover_all_of_them():
    config = DiagnosisConfig().to_dict()
    legacy = _legacy()
    assert len(legacy) == 29
    missing = sorted(set(legacy) - set(config))
    assert missing == [], f"policy_config has DIAG_ constants the config does not carry: {missing}"
    different = {k: (config[k], v) for k, v in legacy.items() if config[k] != (list(v) if isinstance(v, tuple) else v)}
    assert different == {}
    assert config["min_observation_rows"] == policy_config.MIN_OBSERVATION_ROWS


def test_the_agreed_bounds_and_defaults():
    config = DiagnosisConfig()
    assert config.bounds == Bounds(4, 3, 60, 400, 1200.0)  # D-19
    assert (config.stop.min_improvement_points, config.stop.consecutive_rounds) == (5.0, 2)
    assert config.multiple_testing == "fdr_by" and config.payload_cap_bytes == 4096  # D-34, D-36


def test_a_config_round_trips_and_its_fingerprint_follows_every_change():
    config = DiagnosisConfig().with_changes(alpha=0.01, bounds=Bounds(max_depth=2))
    again = DiagnosisConfig.from_dict(config.to_dict())
    assert again == config and again.fingerprint() == config.fingerprint()
    assert DiagnosisConfig().fingerprint() != config.fingerprint()
    assert DiagnosisConfig.from_dict({}) == DiagnosisConfig()


@pytest.mark.parametrize(
    "bad",
    [{"alpha": 1.5}, {"multiple_testing": "none"}, {"history_days": 0}, {"schema_version": 2}, {"surprise": 1},
     {"bounds": {"max_depth": 0}}],
)
def test_invalid_config_is_refused_loudly(bad):
    with pytest.raises((InvalidConfig, TypeError)):
        DiagnosisConfig.from_dict(bad)


def test_streams_are_reproducible_independent_and_not_salted_per_process():
    a, b = SeededRuntime(7), SeededRuntime(7)
    assert np.array_equal(a.rng("when").random(5), b.rng("when").random(5))
    assert not np.array_equal(a.rng("when").random(5), a.rng("gcm").random(5))
    assert not np.array_equal(SeededRuntime(8).rng("when").random(5), a.rng("when").random(5))
    # literal values pin the derivation: changing the scheme would break replay of every stored record
    assert SeededRuntime(7).rng("when").integers(0, 10**9) == 755295312
    assert SeededRuntime(0).rng("x").integers(0, 1000, 3).tolist() == [949, 407, 858]


def test_the_runtime_serves_the_injected_clock_and_cancel_flag():
    flag = {"stop": False}
    instant = datetime(2026, 10, 16, tzinfo=UTC)
    runtime = SeededRuntime(1, clock=lambda: instant, is_cancelled=lambda: flag["stop"])
    assert runtime.now() == instant and runtime.cancelled() is False
    flag["stop"] = True
    assert runtime.cancelled() is True
