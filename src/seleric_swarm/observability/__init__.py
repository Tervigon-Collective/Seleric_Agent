from seleric_swarm.observability._stages import configure_tracing, stage_span

# A9 public interface: exactly the two entry points. The existing agent imports
# the legacy helpers (traced_span, operation_span, …) from submodules
# (seleric_swarm.observability.tracing / .traces / .flow), not from this root.
__all__ = [
    "configure_tracing",
    "stage_span",
]
