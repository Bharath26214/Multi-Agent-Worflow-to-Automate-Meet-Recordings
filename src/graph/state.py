from __future__ import annotations

from typing import List, Optional, TypedDict

from core.models import DraftJiraTicket, JiraTicketsBatch


class GraphState(TypedDict):
    raw_recording_text: str
    extracted_tasks: List[dict]
    jira_tickets_batch: JiraTicketsBatch
    draft_tickets: List[DraftJiraTicket]
    draft_tickets_for_review: List[DraftJiraTicket]
    approved_draft_tickets_batch: JiraTicketsBatch
    rejected_draft_tickets: List[DraftJiraTicket]
    review_index: int
    current_draft_ticket: Optional[DraftJiraTicket]
    review_action: str
    review_edit_prompt: str

