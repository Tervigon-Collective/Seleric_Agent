"""Swarm provider ports (anomaly detectors / metric reading types).

Callers import leaf modules directly to avoid package-load cycles:

    from seleric_swarm.swarm.providers.base import ProviderBundle
    from seleric_swarm.swarm.providers.template import RelativeEffectAnomalyDetector
"""
