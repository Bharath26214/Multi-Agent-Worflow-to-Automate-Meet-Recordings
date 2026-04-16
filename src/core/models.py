from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class ExtractedTask(BaseModel):
    """Canonical task extracted from a meeting transcript."""

    event_id: str = Field(description="Unique task event id, e.g. EVT-00001")
    type: Literal["task"] = "task"
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


class ExtractorOutput(BaseModel):
    """Output of the extractor agent."""

    tasks: List[ExtractedTask]


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


