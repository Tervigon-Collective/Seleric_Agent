"""Scenario loading errors for fixture providers."""


class ScenarioNotFoundError(ValueError):
    """Raised when a fixture scenario id does not exist on disk."""

    def __init__(self, scenario_id: str, *, available: list[str] | None = None) -> None:
        self.scenario_id = scenario_id
        self.available = list(available or [])
        hint = f" Available: {', '.join(self.available)}" if self.available else ""
        super().__init__(f"Unknown scenario_id={scenario_id!r}.{hint}")
