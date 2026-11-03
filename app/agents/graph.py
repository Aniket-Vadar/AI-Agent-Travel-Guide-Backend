"""The planning graph.

                                  ┌─► storyteller ─┐
    START ─► researcher ─► curator ├─► navigator ───┤─► day_planner ─► END
                                  └─► editor ──────┘

Storytelling, transport and front matter have no dependency on each other, so
LangGraph fans them out into one superstep. The day planner joins them back
together because it needs the places, the arrival mode and the food list before
it can lay out a day. On a twelve-place guide that fan-out is the difference
between roughly thirty seconds and roughly twelve.
"""

from __future__ import annotations

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from app.agents.nodes import (
    curator_node,
    editor_node,
    navigator_node,
    planner_node,
    researcher_node,
    storyteller_node,
)
from app.agents.state import PlannerState
from app.core.logging import get_logger
from app.core.tracing import configure_tracing

log = get_logger("graph")

NODE_LABELS: dict[str, str] = {
    "researcher": "Researching the destination",
    "curator": "Choosing the places worth your time",
    "storyteller": "Writing the story of each place",
    "navigator": "Working out routes and local transport",
    "editor": "Writing the overview, food and safety notes",
    "day_planner": "Laying out your day-by-day itinerary",
}

# Rough share of total wall time per node, used to drive the progress bar.
NODE_PROGRESS: dict[str, float] = {
    "researcher": 0.25,
    "curator": 0.45,
    "navigator": 0.62,
    "editor": 0.72,
    "storyteller": 0.88,
    "day_planner": 0.98,
}


def build_graph() -> StateGraph:
    graph = StateGraph(PlannerState)

    graph.add_node("researcher", researcher_node)
    graph.add_node("curator", curator_node)
    graph.add_node("storyteller", storyteller_node)
    graph.add_node("navigator", navigator_node)
    graph.add_node("editor", editor_node)
    graph.add_node("day_planner", planner_node)

    graph.add_edge(START, "researcher")
    graph.add_edge("researcher", "curator")

    # Fan out: three independent agents work on the same curated place list.
    graph.add_edge("curator", "storyteller")
    graph.add_edge("curator", "navigator")
    graph.add_edge("curator", "editor")

    # Join: the day planner needs all three before it can lay out the days.
    graph.add_edge("storyteller", "day_planner")
    graph.add_edge("navigator", "day_planner")
    graph.add_edge("editor", "day_planner")

    graph.add_edge("day_planner", END)
    return graph


@lru_cache(maxsize=1)
def get_compiled_graph():
    """Compile once. The graph is stateless, so a single instance serves everyone."""
    configure_tracing()
    compiled = build_graph().compile()
    log.info("graph_compiled", nodes=list(NODE_LABELS))
    return compiled
