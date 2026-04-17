from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Dict, List

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

SRC_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC_ROOT))

from agents.extractor_agent import ExtractorAgent  # noqa: E402
from agents.jira_builder_agent import JiraBuilderAgent  # noqa: E402
from agents.review_agent import ReviewAgent  # noqa: E402
from core.models import DraftJiraTicket, ExtractedTask, JiraTicketsBatch  # noqa: E402
from utils.logger import get_logger  # noqa: E402

logger = get_logger(__name__)
WORKSPACE_ROOT = SRC_ROOT.parent
FRONTEND_DIR = WORKSPACE_ROOT / "frontend"

app = FastAPI(title="Meet-Jira Local UI API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class StartSessionRequest(BaseModel):
    transcript: str = Field(min_length=1)


class EditDraftRequest(BaseModel):
    instruction: str = Field(min_length=1)


class SessionState(BaseModel):
    extracted_tasks: List[dict]
    ready_tickets: JiraTicketsBatch
    draft_tickets: List[DraftJiraTicket]
    approved_draft_tickets: JiraTicketsBatch
    rejected_draft_tickets: List[DraftJiraTicket]


SESSIONS: Dict[str, SessionState] = {}


def _serialize_session(session_id: str, state: SessionState) -> dict:
    return {
        "session_id": session_id,
        "extracted_tasks": state.extracted_tasks,
        "ready_tickets": state.ready_tickets.model_dump(),
        "draft_tickets": [d.model_dump() for d in state.draft_tickets],
        "approved_draft_tickets": state.approved_draft_tickets.model_dump(),
        "rejected_draft_tickets": [d.model_dump() for d in state.rejected_draft_tickets],
    }


def _get_session(session_id: str) -> SessionState:
    state = SESSIONS.get(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")
    return state


def _raise_external_service_error(stage: str, exc: Exception) -> None:
    logger.exception("%s failed: %s", stage, exc)
    detail = (
        f"{stage} failed due to external API connectivity issue. "
        "Please verify OPENAI_API_KEY, internet/proxy settings, and try again."
    )
    raise HTTPException(status_code=503, detail=detail) from exc


@app.post("/api/sessions")
def start_session(request: StartSessionRequest) -> dict:
    logger.info("Starting UI session")
    extractor = ExtractorAgent()
    jira_builder = JiraBuilderAgent()
    try:
        extracted = extractor.extract_tasks_from_text(request.transcript)
        tasks = [ExtractedTask.model_validate(t.model_dump()) for t in extracted.tasks]
        review_queue = jira_builder.build_jira_review_queue(tasks)
    except HTTPException:
        raise
    except Exception as exc:
        _raise_external_service_error("Transcript processing", exc)

    state = SessionState(
        extracted_tasks=[t.model_dump() for t in extracted.tasks],
        ready_tickets=review_queue.ready_batch,
        draft_tickets=review_queue.draft_tickets,
        approved_draft_tickets=JiraTicketsBatch(tickets=[]),
        rejected_draft_tickets=[],
    )
    session_id = str(uuid.uuid4())
    SESSIONS[session_id] = state
    return _serialize_session(session_id, state)


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str) -> dict:
    state = _get_session(session_id)
    return _serialize_session(session_id, state)


@app.post("/api/sessions/{session_id}/drafts/{event_id}/edit")
def edit_draft(session_id: str, event_id: str, request: EditDraftRequest) -> dict:
    state = _get_session(session_id)
    review_agent = ReviewAgent()

    idx = next((i for i, d in enumerate(state.draft_tickets) if d.event_id == event_id), -1)
    if idx == -1:
        raise HTTPException(status_code=404, detail="Draft not found")

    current = state.draft_tickets[idx]
    try:
        instruction = review_agent.interpret_edit_prompt(current, request.instruction)
        updated = review_agent.apply_edit_to_draft(current, instruction)
    except HTTPException:
        raise
    except Exception as exc:
        _raise_external_service_error("Draft edit", exc)
    state.draft_tickets[idx] = updated
    return _serialize_session(session_id, state)


@app.post("/api/sessions/{session_id}/drafts/{event_id}/approve")
def approve_draft(session_id: str, event_id: str) -> dict:
    state = _get_session(session_id)
    idx = next((i for i, d in enumerate(state.draft_tickets) if d.event_id == event_id), -1)
    if idx == -1:
        raise HTTPException(status_code=404, detail="Draft not found")

    draft = state.draft_tickets.pop(idx)
    approved = list(state.approved_draft_tickets.tickets)
    approved.append(draft.payload)
    state.approved_draft_tickets = JiraTicketsBatch(tickets=approved)
    return _serialize_session(session_id, state)


@app.post("/api/sessions/{session_id}/drafts/{event_id}/reject")
def reject_draft(session_id: str, event_id: str) -> dict:
    state = _get_session(session_id)
    idx = next((i for i, d in enumerate(state.draft_tickets) if d.event_id == event_id), -1)
    if idx == -1:
        raise HTTPException(status_code=404, detail="Draft not found")

    draft = state.draft_tickets.pop(idx)
    rejected = list(state.rejected_draft_tickets)
    rejected.append(draft)
    state.rejected_draft_tickets = rejected
    return _serialize_session(session_id, state)


@app.post("/api/sessions/{session_id}/raise")
def raise_tickets(session_id: str) -> dict:
    state = _get_session(session_id)
    jira_builder = JiraBuilderAgent()
    combined = JiraTicketsBatch(
        tickets=state.ready_tickets.tickets + state.approved_draft_tickets.tickets
    )
    try:
        results = jira_builder.create_jira_issues(combined)
    except HTTPException:
        raise
    except Exception as exc:
        _raise_external_service_error("Jira ticket creation", exc)
    return {
        "session_id": session_id,
        "tickets_raised": len(combined.tickets),
        "jira_create_results": results,
    }


if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR)), name="assets")


@app.get("/")
def serve_ui() -> FileResponse:
    if not FRONTEND_DIR.exists():
        raise HTTPException(status_code=404, detail="frontend directory not found")
    return FileResponse(str(FRONTEND_DIR / "index.html"))

