from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from dotenv import load_dotenv

load_dotenv()


class JiraTicketsBatch(BaseModel):
    """Wrapper so all Jira issues are grouped in one typed Pydantic object."""

    class JiraADFDocument(BaseModel):
        """Minimal Atlassian Document Format (ADF) document for Jira Cloud."""

        type: Literal["doc"] = "doc"
        version: int = 1
        content: List[Dict[str, Any]]

    class JiraIssueFields(BaseModel):
        project: str
        summary: str
        description: "JiraTicketsBatch.JiraADFDocument"
        issuetype: str
        labels: List[str]
        duedate: Optional[str] = None
        priority: Optional[str] = None
        assignee: Optional[str] = None

    class JiraCreateIssuePayload(BaseModel):
        fields: "JiraTicketsBatch.JiraIssueFields"

    tickets: List["JiraTicketsBatch.JiraCreateIssuePayload"]


class ExtractedTask(BaseModel):
    """Canonical task event schema for Jira-focused MVP."""

    event_id: str = Field(description="Unique task event id, e.g. EVT-00001")
    type: Literal["task"] = "task"
    spoken_by: str = Field(description="Speaker who said the task")
    spoken_to: List[str] = Field(default_factory=list, description="People addressed")
    description: str = Field(description="Description of the task")
    assigned_by: str = Field(description="Person assigning the task")
    assigned_to: List[str] = Field(
        default_factory=list, description="Task owners; at least one expected"
    )
    due_date: str | None = Field(
        default=None, description="YYYY-MM-DD if explicitly mentioned"
    )
    priority: Literal["high", "medium", "low"] | None = None
    t0: float = Field(description="Start timestamp in seconds")
    t1: float = Field(description="End timestamp in seconds")
    created_by: Literal["meet-agent"] = "meet-agent"
    confidence: float = Field(ge=0, le=1, description="Extraction confidence score")


class ExtractorOutput(BaseModel):
    tasks: List[ExtractedTask]


class GraphState(TypedDict):
    raw_recording_text: str
    extracted_tasks: List[dict]
    jira_tickets_batch: JiraTicketsBatch


SYSTEM_PROMPT = """You are an expert meeting transcript extraction agent.
Extract ONLY actionable task items from the transcript.

Rules:
1) Return only type="task" items.
2) Ignore greetings, status updates, and generic chat.
3) If task ownership is unclear, do not invent names.
4) Use lowercase priorities: high | medium | low when explicitly implied or stated.
5) due_date must be YYYY-MM-DD when available, else null.
6) t0 and t1 should represent approximate seconds from transcript markers.
7) Keep descriptions concise and action-oriented.
8) created_by must be "meet-agent".
9) Confidence should be 0-1.
"""


def _extract_tasks_node(state: GraphState) -> GraphState:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set. Please export it before running this script.")

    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    llm = ChatOpenAI(model=model, temperature=0)
    structured_llm = llm.with_structured_output(ExtractorOutput)

    user_prompt = (
        "Transcript:\n"
        f"{state['raw_recording_text']}\n\n"
        "Return extracted tasks as structured output."
    )

    result: ExtractorOutput = structured_llm.invoke(
        [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=user_prompt)]
    )

    extracted_task_dicts = [task.model_dump() for task in result.tasks]

    payloads: List[dict] = []
    payload_models: List[JiraTicketsBatch.JiraCreateIssuePayload] = []
    for raw_task in extracted_task_dicts:
        task = ExtractedTask.model_validate(raw_task)

        priority_obj: Optional[JiraTicketsBatch.JiraPriority] = None
        if task.priority:
            priority_obj = JiraTicketsBatch.JiraPriority(
                name=task.priority.title()
            )

        fields: Dict[str, Any] = {
            "project": {"key": os.getenv("JIRA_PROJECT_KEY", "KAN")},
            "summary": task.description.strip(),
            "description": _build_adf_description(task),
            "issuetype": {"name": "Bug" if _looks_like_bug(task.description) else "Task"},
            "labels": [
                "created-by-meet-agent",
                f"event_id:{task.event_id}",
            ],
        }

        if task.due_date:
            fields["duedate"] = task.due_date
        if priority_obj:
            fields["priority"] = priority_obj

        account_id = _resolve_assignee_account_id(task)
        if account_id:
            fields["assignee"] = {"accountId": account_id}
        else:
            if task.assigned_to:
                fields["labels"].append(
                    f"unresolved-assignee:{str(task.assigned_to[0]).lower()}"
                )

        payload_model = JiraTicketsBatch.JiraCreateIssuePayload(
            fields=JiraTicketsBatch.JiraIssueFields.model_validate(fields)
        )
        payload_models.append(payload_model)
        payloads.append(payload_model.model_dump())

    return {
        "extracted_tasks": extracted_task_dicts,
        "jira_tickets_batch": JiraTicketsBatch(tickets=payload_models),
    }


def _build_adf_description(task: ExtractedTask) -> Dict[str, Any]:
    # Minimal ADF that works for Jira Cloud "description".
    text_lines: List[str] = [
        task.description,
        "",
        f"Spoken by: {task.spoken_by}",
        f"Spoken to: {', '.join(task.spoken_to) if task.spoken_to else 'N/A'}",
        f"Assigned by: {task.assigned_by}",
        f"Assigned to: {', '.join(task.assigned_to) if task.assigned_to else 'N/A'}",
        f"Due date: {task.due_date or 'N/A'}",
        f"Priority: {task.priority or 'N/A'}",
        f"Time window: {task.t0:.1f}s - {task.t1:.1f}s",
        f"Event id: {task.event_id}",
    ]
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": "\n".join(text_lines)}],
            }
        ],
    }


def _looks_like_bug(description: str) -> bool:
    d = description.lower()
    return any(k in d for k in ["bug", "error", "exception", "fix", "login", "throws"])


def _load_assignee_accountid_map() -> Dict[str, str]:
    """
    Optional JSON mapping for Jira user resolution.

    Expected format:
      {"x": "accountId1", "x@company.com": "accountId2"}
    """
    raw = os.getenv("JIRA_ASSIGNEE_ACCOUNTID_MAP_JSON", "")
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return {}
        return {str(k).lower(): str(v) for k, v in parsed.items()}
    except json.JSONDecodeError:
        return {}


def _resolve_assignee_account_id(task: ExtractedTask) -> Optional[str]:
    mapping = _load_assignee_accountid_map()
    if not mapping:
        return None
    for name in task.assigned_to:
        key = str(name).lower()
        if key in mapping:
            return mapping[key]
    # Also try assigned_by as a fallback if mapping contains it.
    key = str(task.assigned_by).lower()
    return mapping.get(key)

def build_graph():
    graph = StateGraph(GraphState)
    graph.add_node("extract_tasks", _extract_tasks_node)
    graph.add_edge(START, "extract_tasks")
    graph.add_edge("extract_tasks", END)
    return graph.compile()


def run_mvp(raw_recording_text: str) -> Dict[str, Any]:
    app = build_graph()
    final_state = app.invoke(
        {
            "raw_recording_text": raw_recording_text,
            "extracted_tasks": [],
            "jira_tickets_batch": JiraTicketsBatch(tickets=[]),
        }
    )
    return {
        "extracted_tasks": final_state["extracted_tasks"],
        "jira_tickets_batch": final_state["jira_tickets_batch"],
    }


if __name__ == "__main__":
    sample_transcript = """
[00:00] John: Good morning team. Let's focus on API integration and release prep.
[00:12] Priya: Yesterday we completed auth middleware tests.
[00:22] John: Alex, please finish Stripe API integration by 2026-04-20. This is high priority.
[00:38] Alex: Got it, I will complete Stripe integration by 2026-04-20.
[00:47] John: Priya, create end-to-end test cases for checkout flow by 2026-04-21. Medium priority.
[01:02] Priya: Sure, I’ll take that.
[01:18] John: Mike, fix webhook retry bug and push a patch by 2026-04-19. High priority.
[01:35] Mike: Okay, I will handle it.
[01:42] John: Alex, update API documentation for payment endpoints by 2026-04-22. Low priority.
[02:05] John: That’s all for today.
""".strip()

    started_at = datetime.now(timezone.utc).isoformat()
    out = run_mvp(sample_transcript)
    print(
        json.dumps(
            {
                "run_started_at": started_at,
                "task_count": len(out["extracted_tasks"]),
                "tasks": out["extracted_tasks"],
                "jira_ticket_count": len(out["jira_tickets_batch"].tickets),
                "jira_tickets_batch": out["jira_tickets_batch"].model_dump(),
            },
            indent=2,
        )
    )
