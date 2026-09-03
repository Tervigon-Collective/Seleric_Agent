from fastapi import FastAPI
from pydantic import BaseModel, Field
from uuid import uuid4

app = FastAPI(title="Seleric Intelligence Swarm", version="0.1.0")


class MissionRequest(BaseModel):
    query: str
    scope: dict = Field(default_factory=dict)
    mode: str = "read_only"


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/v1/missions")
def create_mission(req: MissionRequest) -> dict:
    # TODO: persist mission, invoke LangGraph orchestration, return durable mission state.
    return {
        "mission_id": f"M-{uuid4().hex[:10]}",
        "status": "planned",
        "query": req.query,
        "mode": req.mode,
    }
