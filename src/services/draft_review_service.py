from __future__ import annotations

import json
import os
from typing import List

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from core.models import DraftJiraTicket, JiraTicketsBatch, TicketEditInstruction
from utils.logger import get_logger

logger = get_logger(__name__)


class DraftReviewService:
    """Service handling human review and AI-assisted edits for draft Jira tickets."""

    @staticmethod
    def _load_assignee_accountid_map() -> dict[str, str]:
        raw = os.getenv("JIRA_ASSIGNEE_ACCOUNTID_MAP_JSON", "")
        if not raw.strip():
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if not isinstance(parsed, dict):
            return {}
        return {str(k).lower(): str(v) for k, v in parsed.items()}

    @staticmethod
    def _to_adf(text: str) -> dict:
        return {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": text.strip()}],
                }
            ],
        }

    def interpret_edit_prompt(
        self,
        draft: DraftJiraTicket,
        user_prompt: str,
    ) -> TicketEditInstruction:
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        llm = ChatOpenAI(model=model, temperature=0)
        structured_llm = llm.with_structured_output(TicketEditInstruction)
        current_payload = json.dumps(draft.payload.model_dump(), indent=2)

        system_prompt = (
            "You are a Jira ticket editor. Convert user instruction into structured edits. "
            "Only fill fields explicitly requested by user. Keep unspecified fields as null."
        )
        user_message = (
            f"Current draft reasons: {', '.join(draft.reasons)}\n"
            f"Current payload:\n{current_payload}\n\n"
            f"User instruction:\n{user_prompt}\n"
        )
        return structured_llm.invoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=user_message)]
        )

    def apply_edit_to_draft(
        self,
        draft: DraftJiraTicket,
        instruction: TicketEditInstruction,
    ) -> DraftJiraTicket:
        payload_dict = draft.payload.model_dump()
        fields = payload_dict["fields"]
        current_reasons = list(draft.reasons)

        if instruction.new_summary:
            fields["summary"] = instruction.new_summary.strip()
        if instruction.new_description:
            fields["description"] = self._to_adf(instruction.new_description)
        if instruction.assign_to:
            name = instruction.assign_to.strip().lower()
            mapping = self._load_assignee_accountid_map()
            account_id = mapping.get(name)
            if account_id:
                fields["assignee"] = {"accountId": account_id}
                current_reasons = [r for r in current_reasons if r != "assignee_not_found"]
            else:
                logger.warning(
                    "Unable to resolve assignee '%s' to Jira accountId from mapping",
                    instruction.assign_to,
                )

        updated_payload = JiraTicketsBatch.JiraCreateIssuePayload.model_validate(payload_dict)
        if instruction.new_description and "task_not_clear" in current_reasons:
            current_reasons = [r for r in current_reasons if r != "task_not_clear"]
        if instruction.add_reason_notes:
            current_reasons.append(f"human_note:{instruction.add_reason_notes.strip()}")

        return DraftJiraTicket(
            event_id=draft.event_id,
            summary=updated_payload.fields.summary,
            reasons=current_reasons,
            payload=updated_payload,
        )

    def human_review_drafts(
        self,
        draft_tickets: List[DraftJiraTicket],
    ) -> tuple[JiraTicketsBatch, List[DraftJiraTicket]]:
        logger.info("Approval Needed for %d draft ticket(s)", len(draft_tickets))
        approved_payloads: List[JiraTicketsBatch.JiraCreateIssuePayload] = []
        rejected: List[DraftJiraTicket] = []

        for draft in draft_tickets:
            current = draft
            while True:
                choice = input(
                    f"\nDraft ticket | Event: {current.event_id} | Summary: {current.summary} | "
                    f"Reasons: {', '.join(current.reasons)}\n"
                    "Choose action: approve / reject / edit [a/r/e]: "
                ).strip().lower()
                if choice in {"a", "approve", "y", "yes"}:
                    approved_payloads.append(current.payload)
                    break
                if choice in {"r", "reject", "n", "no", ""}:
                    rejected.append(current)
                    break
                if choice in {"e", "edit"}:
                    edit_prompt = input("Enter edit instruction for this draft: ").strip()
                    if not edit_prompt:
                        continue
                    try:
                        instruction = self.interpret_edit_prompt(current, edit_prompt)
                        current = self.apply_edit_to_draft(current, instruction)
                        logger.info(
                            "Draft %s edited via prompt; updated reasons: %s",
                            current.event_id,
                            ", ".join(current.reasons) if current.reasons else "none",
                        )
                    except Exception as exc:
                        logger.exception("Failed to edit draft %s: %s", current.event_id, exc)
                    continue

        logger.info(
            "Human review complete: %d approved, %d rejected",
            len(approved_payloads),
            len(rejected),
        )
        return JiraTicketsBatch(tickets=approved_payloads), rejected

