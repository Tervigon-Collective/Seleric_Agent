"""Experiment statistics — sample size and observed lift.

Sample size is real power analysis via ``statsmodels.stats.power``, not an
approximation. ``statsmodels>=0.14`` is already a declared dependency (added
in Sprint 3 for the forecaster), so this costs nothing new; scipy is not
needed.

Lift deliberately reuses the tri-state discipline from
``models/evaluation.py``: a result whose interval is unknown is reported as
unknown rather than as "not significant". Collapsing those two would let an
experiment that was never powered look like an experiment that found nothing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from seleric_swarm.toolsets import policy_config as policy


class SampleSizeUnavailable(Exception):
    """Inputs cannot yield a meaningful sample size. Carries the policy warning
    so the toolset can name which gate declined."""

    def __init__(self, message: str, *, warning: str) -> None:
        super().__init__(message)
        self.warning = warning


@dataclass(frozen=True)
class SampleSizeResult:
    per_variant: int
    total: int
    baseline_rate: float
    mde: float
    power: float
    alpha: float
    effect_size: float


@dataclass(frozen=True)
class Lift:
    variant: str
    control_value: float
    variant_value: float
    absolute: float
    relative: float | None
    #: None when no interval was available — unknown, not "no effect".
    significant: bool | None = None


def estimate_sample_size(
    baseline_rate: float,
    mde: float,
    *,
    power: float = policy.DEFAULT_POWER,
    alpha: float = policy.DEFAULT_ALPHA,
) -> SampleSizeResult:
    """Per-variant sample size for a two-proportion test.

    ``mde`` is the **absolute** difference in rate to detect: a baseline of
    0.02 with ``mde=0.005`` sizes for detecting 2.0% → 2.5%. Stated explicitly
    because relative-vs-absolute MDE is the most common way a sample-size
    number ends up silently 4x wrong.
    """
    if not 0 < baseline_rate < 1:
        raise SampleSizeUnavailable(
            f"baseline_rate must be a proportion strictly between 0 and 1, got {baseline_rate}",
            warning=policy.WARN_INVALID_RATE,
        )
    if mde < policy.MIN_DETECTABLE_EFFECT:
        raise SampleSizeUnavailable(
            f"mde {mde} is below {policy.MIN_DETECTABLE_EFFECT}; required sample size "
            "grows without useful bound",
            warning=policy.WARN_INVALID_RATE,
        )
    if not 0 < power < 1:
        raise SampleSizeUnavailable(
            f"power must be between 0 and 1, got {power}", warning=policy.WARN_INVALID_RATE
        )
    treated_rate = baseline_rate + mde
    if not 0 < treated_rate < 1:
        raise SampleSizeUnavailable(
            f"baseline_rate + mde = {treated_rate} leaves the 0-1 range; no proportion "
            "test can detect a rate above 1",
            warning=policy.WARN_INVALID_RATE,
        )

    try:
        from statsmodels.stats.power import NormalIndPower
        from statsmodels.stats.proportion import proportion_effectsize
    except ImportError as exc:  # pragma: no cover - declared dependency
        raise SampleSizeUnavailable(
            f"statsmodels unavailable: {exc}", warning=policy.WARN_MODEL_UNAVAILABLE
        ) from exc

    # Cohen's h, the effect size a two-proportion power calculation works in.
    effect_size = float(proportion_effectsize(treated_rate, baseline_rate))
    if effect_size == 0:
        raise SampleSizeUnavailable(
            "effect size is zero; nothing to detect", warning=policy.WARN_INVALID_RATE
        )

    needed = NormalIndPower().solve_power(
        effect_size=abs(effect_size), power=power, alpha=alpha, ratio=1.0, alternative="two-sided"
    )
    # math.ceil returns an int in Python 3; no second cast needed.
    per_variant = math.ceil(float(needed))
    return SampleSizeResult(
        per_variant=per_variant,
        total=per_variant * 2,
        baseline_rate=baseline_rate,
        mde=mde,
        power=power,
        alpha=alpha,
        effect_size=effect_size,
    )


def lift(*, variant: str, control_value: float, variant_value: float) -> Lift:
    """Observed absolute and relative lift.

    ``significant`` is left ``None``: the evidence artifacts this is computed
    from carry point values with no interval or sample count, so significance
    is genuinely unknown here. Returning ``False`` would read as "we tested
    and it wasn't significant", which is a different and unearned claim.
    """
    absolute = variant_value - control_value
    relative = (absolute / control_value) if control_value else None
    return Lift(
        variant=variant,
        control_value=control_value,
        variant_value=variant_value,
        absolute=absolute,
        relative=relative,
        significant=None,
    )
