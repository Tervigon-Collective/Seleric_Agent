"""Swarm provider ports.

Submodules import from each other in a chain that must not be flattened here:
``mcp_data`` reaches into ``swarm.domain.configs`` which in turn imports
``swarm.domain.base``, which imports ``providers.base``. Re-exporting
``mcp_data`` from this ``__init__`` creates a cycle at package-load time, so
callers must import from the leaf module directly:

    from seleric_swarm.swarm.providers.base import ProviderBundle
    from seleric_swarm.swarm.providers.mcp_data import build_hybrid_bundle
    from seleric_swarm.swarm.providers.template import RelativeEffectAnomalyDetector
"""
