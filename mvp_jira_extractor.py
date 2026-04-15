from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import List, Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from dotenv import load_dotenv

load_dotenv()

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
        raise RuntimeError(
            "OpenAI API key is not set. Please export it before running this script."
        )

    llm = ChatOpenAI(model="gpt-5-mini", temperature=0)
    structured_llm = llm.with_structured_output(ExtractorOutput)

    user_prompt = (
        "Transcript:\n"
        f"{state['raw_recording_text']}\n\n"
        "Return extracted tasks as structured output."
    )

    result: ExtractorOutput = structured_llm.invoke(
        [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=user_prompt)]
    )

    return {"extracted_tasks": [task.model_dump() for task in result.tasks]}


def build_graph():
    graph = StateGraph(GraphState)
    graph.add_node("extract_tasks", _extract_tasks_node)
    graph.add_edge(START, "extract_tasks")
    graph.add_edge("extract_tasks", END)
    return graph.compile()


def run_mvp(raw_recording_text: str) -> List[dict]:
    app = build_graph()
    final_state = app.invoke({"raw_recording_text": raw_recording_text, "extracted_tasks": []})
    return final_state["extracted_tasks"]


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
    tasks = run_mvp(sample_transcript)
    print(
        json.dumps(
            {
                "run_started_at": started_at,
                "task_count": len(tasks),
                "tasks": tasks,
            },
            indent=2,
        )
    )
