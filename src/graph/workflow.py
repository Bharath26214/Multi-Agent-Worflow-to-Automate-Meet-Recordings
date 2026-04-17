from __future__ import annotations

from typing import List

from langgraph.graph import END, START, StateGraph

from agents.extractor_agent import ExtractorAgent
from agents.jira_builder_agent import JiraBuilderAgent
from agents.review_agent import ReviewAgent
from core.models import (
    DraftJiraTicket,
    ExtractedTask,
    ExtractorOutput,
    JiraReviewQueue,
    JiraTicketsBatch,
)
from graph.state import GraphState
from utils.logger import get_logger

logger = get_logger(__name__)


def _extract_tasks_node(state: GraphState) -> GraphState:
    logger.info("Extracting Tasks")
    extractor = ExtractorAgent()
    result: ExtractorOutput = extractor.extract_tasks_from_text(
        state["raw_recording_text"]
    )
    return {
        "extracted_tasks": [t.model_dump() for t in result.tasks],
        "jira_tickets_batch": state["jira_tickets_batch"],
        "draft_tickets": state["draft_tickets"],
        "draft_tickets_for_review": state["draft_tickets_for_review"],
        "approved_draft_tickets_batch": state["approved_draft_tickets_batch"],
        "rejected_draft_tickets": state["rejected_draft_tickets"],
        "review_index": state["review_index"],
        "current_draft_ticket": state["current_draft_ticket"],
        "review_action": state["review_action"],
        "review_edit_prompt": state["review_edit_prompt"],
        "raw_recording_text": state["raw_recording_text"],
    }


def _build_jira_tickets_node(state: GraphState) -> GraphState:
    logger.info("Creating Jira Tickets")
    tasks: List[ExtractedTask] = [
        ExtractedTask.model_validate(t) for t in state["extracted_tasks"]
    ]
    jira_builder = JiraBuilderAgent()
    review_queue: JiraReviewQueue = jira_builder.build_jira_review_queue(tasks)
    batch: JiraTicketsBatch = review_queue.ready_batch
    draft_tickets: List[DraftJiraTicket] = review_queue.draft_tickets
    return {
        "extracted_tasks": state["extracted_tasks"],
        "jira_tickets_batch": batch,
        "draft_tickets": draft_tickets,
        "draft_tickets_for_review": state["draft_tickets_for_review"],
        "approved_draft_tickets_batch": state["approved_draft_tickets_batch"],
        "rejected_draft_tickets": state["rejected_draft_tickets"],
        "review_index": state["review_index"],
        "current_draft_ticket": state["current_draft_ticket"],
        "review_action": state["review_action"],
        "review_edit_prompt": state["review_edit_prompt"],
        "raw_recording_text": state["raw_recording_text"],
    }


def _prepare_draft_review_node(state: GraphState) -> GraphState:
    logger.info("Preparing draft tickets for review")
    return {
        "extracted_tasks": state["extracted_tasks"],
        "jira_tickets_batch": state["jira_tickets_batch"],
        "draft_tickets": state["draft_tickets"],
        "draft_tickets_for_review": state["draft_tickets"],
        "approved_draft_tickets_batch": JiraTicketsBatch(tickets=[]),
        "rejected_draft_tickets": [],
        "review_index": 0,
        "current_draft_ticket": None,
        "review_action": "",
        "review_edit_prompt": "",
        "raw_recording_text": state["raw_recording_text"],
    }


def _select_next_draft_node(state: GraphState) -> GraphState:
    idx = state["review_index"]
    queue = state["draft_tickets_for_review"]
    current = queue[idx] if idx < len(queue) else None
    return {
        "extracted_tasks": state["extracted_tasks"],
        "jira_tickets_batch": state["jira_tickets_batch"],
        "draft_tickets": state["draft_tickets"],
        "draft_tickets_for_review": state["draft_tickets_for_review"],
        "approved_draft_tickets_batch": state["approved_draft_tickets_batch"],
        "rejected_draft_tickets": state["rejected_draft_tickets"],
        "review_index": state["review_index"],
        "current_draft_ticket": current,
        "review_action": "",
        "review_edit_prompt": "",
        "raw_recording_text": state["raw_recording_text"],
    }


def _select_next_route(state: GraphState) -> str:
    return "collect_review_decision" if state["current_draft_ticket"] else "end_review"


def _collect_review_decision_node(state: GraphState) -> GraphState:
    draft = state["current_draft_ticket"]
    if not draft:
        return state
    edit_prompt = ""
    normalized_action = ""
    while not normalized_action:
        choice = input(
            f"\nDraft ticket | Event: {draft.event_id} | Summary: {draft.summary} | "
            f"Reasons: {', '.join(draft.reasons)}\n"
            "Choose action: approve / reject / edit [a/r/e]\n"
            "(Tip: you can also type an edit instruction directly): "
        ).strip()
        lowered = choice.lower()

        if lowered in {"a", "approve", "y", "yes"}:
            normalized_action = "approve"
            break
        if lowered in {"r", "reject", "n", "no"}:
            normalized_action = "reject"
            break
        if lowered in {"e", "edit"}:
            edit_prompt = input("Enter edit instruction for this draft: ").strip()
            if edit_prompt:
                normalized_action = "edit"
            else:
                logger.info("Edit selected but no instruction provided; ask again")
            continue

        # If user types a free-form sentence, treat it as edit instruction.
        if choice:
            normalized_action = "edit"
            edit_prompt = choice
            break

        logger.info("Invalid choice. Please enter approve/reject/edit.")

    return {
        "extracted_tasks": state["extracted_tasks"],
        "jira_tickets_batch": state["jira_tickets_batch"],
        "draft_tickets": state["draft_tickets"],
        "draft_tickets_for_review": state["draft_tickets_for_review"],
        "approved_draft_tickets_batch": state["approved_draft_tickets_batch"],
        "rejected_draft_tickets": state["rejected_draft_tickets"],
        "review_index": state["review_index"],
        "current_draft_ticket": state["current_draft_ticket"],
        "review_action": normalized_action,
        "review_edit_prompt": edit_prompt,
        "raw_recording_text": state["raw_recording_text"],
    }


def _decision_route(state: GraphState) -> str:
    action = state["review_action"]
    if action == "approve":
        return "approve_draft"
    if action == "edit":
        return "review_agent_edit"
    return "reject_draft"


def _review_agent_edit_node(state: GraphState) -> GraphState:
    draft = state["current_draft_ticket"]
    if not draft:
        return state
    if not state["review_edit_prompt"]:
        logger.info("No edit prompt provided; returning to decision step")
        return state

    review_agent = ReviewAgent()
    instruction = review_agent.interpret_edit_prompt(draft, state["review_edit_prompt"])
    edited_draft = review_agent.apply_edit_to_draft(draft, instruction)
    logger.info(
        "Draft %s edited by review agent; reasons now: %s",
        edited_draft.event_id,
        ", ".join(edited_draft.reasons) if edited_draft.reasons else "none",
    )
    return {
        "extracted_tasks": state["extracted_tasks"],
        "jira_tickets_batch": state["jira_tickets_batch"],
        "draft_tickets": state["draft_tickets"],
        "draft_tickets_for_review": state["draft_tickets_for_review"],
        "approved_draft_tickets_batch": state["approved_draft_tickets_batch"],
        "rejected_draft_tickets": state["rejected_draft_tickets"],
        "review_index": state["review_index"],
        "current_draft_ticket": edited_draft,
        "review_action": "",
        "review_edit_prompt": "",
        "raw_recording_text": state["raw_recording_text"],
    }


def _post_edit_route(state: GraphState) -> str:
    draft = state["current_draft_ticket"]
    if draft and not draft.reasons:
        return "approve_draft"
    return "collect_review_decision"


def _approve_draft_node(state: GraphState) -> GraphState:
    draft = state["current_draft_ticket"]
    approved = list(state["approved_draft_tickets_batch"].tickets)
    if draft:
        approved.append(draft.payload)
    return {
        "extracted_tasks": state["extracted_tasks"],
        "jira_tickets_batch": state["jira_tickets_batch"],
        "draft_tickets": state["draft_tickets"],
        "draft_tickets_for_review": state["draft_tickets_for_review"],
        "approved_draft_tickets_batch": JiraTicketsBatch(tickets=approved),
        "rejected_draft_tickets": state["rejected_draft_tickets"],
        "review_index": state["review_index"] + 1,
        "current_draft_ticket": None,
        "review_action": "",
        "review_edit_prompt": "",
        "raw_recording_text": state["raw_recording_text"],
    }


def _reject_draft_node(state: GraphState) -> GraphState:
    draft = state["current_draft_ticket"]
    rejected = list(state["rejected_draft_tickets"])
    if draft:
        rejected.append(draft)
    return {
        "extracted_tasks": state["extracted_tasks"],
        "jira_tickets_batch": state["jira_tickets_batch"],
        "draft_tickets": state["draft_tickets"],
        "draft_tickets_for_review": state["draft_tickets_for_review"],
        "approved_draft_tickets_batch": state["approved_draft_tickets_batch"],
        "rejected_draft_tickets": rejected,
        "review_index": state["review_index"] + 1,
        "current_draft_ticket": None,
        "review_action": "",
        "review_edit_prompt": "",
        "raw_recording_text": state["raw_recording_text"],
    }


def _end_review_node(state: GraphState) -> GraphState:
    logger.info(
        "Draft review complete: %d approved, %d rejected",
        len(state["approved_draft_tickets_batch"].tickets),
        len(state["rejected_draft_tickets"]),
    )
    return {
        "extracted_tasks": state["extracted_tasks"],
        "jira_tickets_batch": state["jira_tickets_batch"],
        "draft_tickets": state["draft_tickets"],
        "draft_tickets_for_review": state["draft_tickets_for_review"],
        "approved_draft_tickets_batch": state["approved_draft_tickets_batch"],
        "rejected_draft_tickets": state["rejected_draft_tickets"],
        "review_index": state["review_index"],
        "current_draft_ticket": state["current_draft_ticket"],
        "review_action": state["review_action"],
        "review_edit_prompt": state["review_edit_prompt"],
        "raw_recording_text": state["raw_recording_text"],
    }


def build_graph():
    graph = StateGraph(GraphState)
    graph.add_node("extract_tasks", _extract_tasks_node)
    graph.add_node("build_jira_tickets", _build_jira_tickets_node)
    graph.add_node("prepare_draft_review", _prepare_draft_review_node)
    graph.add_node("select_next_draft", _select_next_draft_node)
    graph.add_node("collect_review_decision", _collect_review_decision_node)
    graph.add_node("review_agent_edit", _review_agent_edit_node)
    graph.add_node("approve_draft", _approve_draft_node)
    graph.add_node("reject_draft", _reject_draft_node)
    graph.add_node("end_review", _end_review_node)
    graph.add_edge(START, "extract_tasks")
    graph.add_edge("extract_tasks", "build_jira_tickets")
    graph.add_edge("build_jira_tickets", "prepare_draft_review")
    graph.add_edge("prepare_draft_review", "select_next_draft")
    graph.add_conditional_edges(
        "select_next_draft",
        _select_next_route,
        {"collect_review_decision": "collect_review_decision", "end_review": "end_review"},
    )
    graph.add_conditional_edges(
        "collect_review_decision",
        _decision_route,
        {
            "approve_draft": "approve_draft",
            "reject_draft": "reject_draft",
            "review_agent_edit": "review_agent_edit",
        },
    )
    graph.add_conditional_edges(
        "review_agent_edit",
        _post_edit_route,
        {
            "approve_draft": "approve_draft",
            "collect_review_decision": "collect_review_decision",
        },
    )
    graph.add_edge("approve_draft", "select_next_draft")
    graph.add_edge("reject_draft", "select_next_draft")
    graph.add_edge("end_review", END)
    return graph.compile()

