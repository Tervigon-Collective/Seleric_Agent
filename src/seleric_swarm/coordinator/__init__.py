"""Seleric Swarm Coordinator package.

Only ``coordinator.observability`` survives here — the mission-event
vocabulary that replaced the Blackboard (see
``coordinator/observability/events.py``). The classify/decompose/intake
pipeline that used to live alongside it was deleted once V3
(``agent/runner.py::run_v3_mission``) became the only mission path.
"""
