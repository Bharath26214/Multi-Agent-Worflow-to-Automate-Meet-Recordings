from __future__ import annotations

from typing import List, TypedDict

from core.models import JiraTicketsBatch


class GraphState(TypedDict):
    raw_recording_text: str
    extracted_tasks: List[dict]
    jira_tickets_batch: JiraTicketsBatch

