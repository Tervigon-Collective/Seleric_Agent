"""DoWhy boundary.

Do not let agents call arbitrary causal estimators without a registered causal question/graph.
"""
from dataclasses import dataclass
from typing import Any


@dataclass
class CausalRequest:
    treatment: str
    outcome: str
    common_causes: list[str]
    graph_id: str
    estimator: str
    refuters: list[str]


class DoWhyService:
    async def estimate(self, request: CausalRequest, data: Any) -> dict:
        # TODO: create CausalModel, identify effect, estimate and run configured refuters.
        raise NotImplementedError
