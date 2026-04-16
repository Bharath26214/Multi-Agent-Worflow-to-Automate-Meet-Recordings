from __future__ import annotations

from typing import List

from langgraph.graph import END, START, StateGraph

from agents.extractor_agent import ExtractorAgent
from agents.jira_builder_agent import JiraBuilderAgent
from core.models import ExtractedTask, ExtractorOutput, JiraTicketsBatch
from graph.state import GraphState


def _extract_tasks_node(state: GraphState) -> GraphState:
    extractor = ExtractorAgent()
    result: ExtractorOutput = extractor.extract_tasks_from_text(
        state["raw_recording_text"]
    )
    return {
        "extracted_tasks": [t.model_dump() for t in result.tasks],
        "jira_tickets_batch": state["jira_tickets_batch"],
        "raw_recording_text": state["raw_recording_text"],
    }


def _build_jira_tickets_node(state: GraphState) -> GraphState:
    tasks: List[ExtractedTask] = [
        ExtractedTask.model_validate(t) for t in state["extracted_tasks"]
    ]
    jira_builder = JiraBuilderAgent()
    batch: JiraTicketsBatch = jira_builder.build_jira_tickets_batch(tasks)
    return {
        "extracted_tasks": state["extracted_tasks"],
        "jira_tickets_batch": batch,
        "raw_recording_text": state["raw_recording_text"],
    }


def build_graph():
    graph = StateGraph(GraphState)
    graph.add_node("extract_tasks", _extract_tasks_node)
    graph.add_node("build_jira_tickets", _build_jira_tickets_node)
    graph.add_edge(START, "extract_tasks")
    graph.add_edge("extract_tasks", "build_jira_tickets")
    graph.add_edge("build_jira_tickets", END)
    return graph.compile()

