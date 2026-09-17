"""Compatibility alias — production envelope is ``swarm.envelope.SwarmMessage``."""

from seleric_swarm.swarm.envelope import SwarmMessage as SwarmEnvelope

__all__ = ["SwarmEnvelope"]
