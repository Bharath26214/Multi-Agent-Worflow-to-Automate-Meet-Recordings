from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class ExtractedTask(BaseModel):
    """Canonical actionable event extracted from a meeting transcript."""

    event_id: str = Field(description="Unique task event id, e.g. EVT-00001")
    type: Literal["task", "meet"] = "task"
    spoken_by: str
    spoken_to: List[str] = Field(default_factory=list, description="People addressed")
    description: str = Field(description="Description of the task")
    assigned_by: str = Field(description="Person assigning the task")
    assigned_to: List[str] = Field(
        default_factory=list, description="Task owners; at least one expected"
    )
    due_date: str | None = Field(default=None, description="YYYY-MM-DD if mentioned")
    priority: Literal["high", "medium", "low"] | None = None
    t0: float = Field(description="Start timestamp in seconds")
    t1: float = Field(description="End timestamp in seconds")
    created_by: Literal["meet-agent"] = "meet-agent"
    confidence: float = Field(ge=0, le=1, description="Extraction confidence score")
    meeting_date: str | None = Field(
        default=None,
        description="Meeting start datetime in ISO format for type='meet'.",
    )
    time_zone: str | None = Field(
        default=None,
        description="Timezone for the meeting event for type='meet'.",
    )


class ExtractorOutput(BaseModel):
    """Output of the extractor agent."""

    tasks: List[ExtractedTask]


class MeetingSummary(BaseModel):
    """Structured summary generated for the full meeting transcript."""

    meet_id: str = Field(
        default="",
        description="Meeting identifier when available.",
    )
    attendees: List[str] = Field(
        default_factory=list,
        description="List of participants who attended the meeting.",
    )
    organizer: str = Field(
        default="",
        description="Meeting organizer/host when available.",
    )
    meeting_timestamp: str = Field(
        default="",
        description="Meeting date/time or timestamp in ISO format when available.",
    )
    summary: str = Field(description="Concise overall meeting summary.")
    key_points: List[str] = Field(default_factory=list)
    action_items: List[str] = Field(
        default_factory=list,
        description="Action items discussed in the meeting, not limited to Jira-ready tasks.",
    )
    future_meetings: List[str] = Field(
        default_factory=list,
        description="Any planned/follow-up meetings mentioned in the transcript.",
    )
    deadlines: List[str] = Field(
        default_factory=list,
        description="Deadline statements or date commitments captured from the meeting.",
    )
    decisions: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    next_steps: List[str] = Field(default_factory=list)


class JiraTicketsBatch(BaseModel):
    """All Jira create-issue payloads grouped in one typed Pydantic object."""

    class JiraADFDocument(BaseModel):
        type: Literal["doc"] = "doc"
        version: int = 1
        content: List[Dict[str, Any]]

    class JiraProject(BaseModel):
        key: str

    class JiraIssueType(BaseModel):
        name: str

    class JiraPriority(BaseModel):
        name: str

    class JiraAssignee(BaseModel):
        accountId: str

    class JiraIssueFields(BaseModel):
        project: "JiraTicketsBatch.JiraProject"
        summary: str = Field(description="Summary of the task")
        description: JiraADFDocument = Field(description="Description of the task in ADF format")
        issuetype: "JiraTicketsBatch.JiraIssueType"
        labels: List[str] = Field(default_factory=list, description="Labels for the task")
        duedate: Optional[str] = Field(default=None, description="YYYY-MM-DD if explicitly mentioned")
        priority: Optional["JiraTicketsBatch.JiraPriority"] = Field(default=None, description="Priority of the task")
        assignee: Optional["JiraTicketsBatch.JiraAssignee"] = Field(default=None, description="Assignee of the task")

    class JiraCreateIssuePayload(BaseModel):
        fields: "JiraTicketsBatch.JiraIssueFields"

    tickets: List["JiraTicketsBatch.JiraCreateIssuePayload"] 


JiraTicketsBatch.model_rebuild()


class DraftJiraTicket(BaseModel):
    """A draft Jira ticket requiring human approval."""

    event_id: str
    summary: str
    reasons: List[str]
    payload: JiraTicketsBatch.JiraCreateIssuePayload


class TicketEditInstruction(BaseModel):
    """Structured edit directives derived from human natural-language prompts."""

    assign_to: Optional[str] = Field(
        default=None, description="Human-readable assignee name to resolve."
    )
    new_summary: Optional[str] = Field(
        default=None, description="Updated Jira summary."
    )
    new_description: Optional[str] = Field(
        default=None, description="Updated plain text description for Jira ADF."
    )
    new_due_date: Optional[str] = Field(
        default=None, description="Updated due date in YYYY-MM-DD format."
    )
    new_priority: Optional[Literal["High", "Medium", "Low"]] = Field(
        default=None, description="Updated Jira priority when explicitly requested."
    )
    remove_reasons: List[str] = Field(
        default_factory=list,
        description="Draft reasons to remove, e.g., low_confidence, task_not_clear.",
    )
    add_reason_notes: Optional[str] = Field(
        default=None, description="Optional notes added by human reviewer."
    )


class JiraReviewQueue(BaseModel):
    """Split output from Jira builder before human-in-the-loop step."""

    ready_batch: JiraTicketsBatch
    draft_tickets: List[DraftJiraTicket] = Field(default_factory=list)


