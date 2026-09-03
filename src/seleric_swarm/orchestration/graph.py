"""LangGraph orchestration skeleton.

Keep internal orchestration here. A2A is used at independent agent/service boundaries.
"""
from langgraph.graph import END, StateGraph
from .state import MissionState


def coordinator_node(state: MissionState) -> MissionState:
    # TODO: normalize query, classify, build task DAG and choose initial mission lead.
    return state


def dispatch_node(state: MissionState) -> MissionState:
    # TODO: route to domain lead + active intelligence specialist.
    return state


def skeptic_gate_node(state: MissionState) -> MissionState:
    # TODO: validate claim provenance and skeptic completion requirements.
    return state


def finalize_node(state: MissionState) -> MissionState:
    # TODO: synthesize only claims that passed policy.
    return state


def build_graph():
    graph = StateGraph(MissionState)
    graph.add_node("coordinator", coordinator_node)
    graph.add_node("dispatch", dispatch_node)
    graph.add_node("skeptic_gate", skeptic_gate_node)
    graph.add_node("finalize", finalize_node)
    graph.set_entry_point("coordinator")
    graph.add_edge("coordinator", "dispatch")
    graph.add_edge("dispatch", "skeptic_gate")
    graph.add_edge("skeptic_gate", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()
