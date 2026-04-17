from __future__ import annotations

import json
import os

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from core.models import DraftJiraTicket, JiraTicketsBatch, TicketEditInstruction
from utils.logger import get_logger

logger = get_logger(__name__)


class ReviewAgent:
    """Agent that interprets human edit prompts and updates draft Jira tickets."""

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
            "Only fill fields explicitly requested by user. Keep unspecified fields as null/empty. "
            "If user asks to remove review flags (e.g. exclude task_not_clear), set remove_reasons accordingly. "
            "If user specifies a due date like 'April 20th', normalize to YYYY-MM-DD in new_due_date."
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
        if instruction.new_due_date:
            fields["duedate"] = instruction.new_due_date.strip()
        if instruction.new_priority:
            fields["priority"] = {"name": instruction.new_priority}
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
        if instruction.remove_reasons:
            to_remove = {r.strip().lower() for r in instruction.remove_reasons if r.strip()}
            current_reasons = [r for r in current_reasons if r.lower() not in to_remove]
        if instruction.add_reason_notes:
            note = f"human_note:{instruction.add_reason_notes.strip()}"
            if note not in current_reasons:
                current_reasons.append(note)

        current_reasons = self._recompute_reasons(updated_payload, current_reasons)

        return DraftJiraTicket(
            event_id=draft.event_id,
            summary=updated_payload.fields.summary,
            reasons=current_reasons,
            payload=updated_payload,
        )

    @staticmethod
    def _is_task_clear(summary: str, description_text: str) -> bool:
        text = f"{summary} {description_text}".strip().lower()
        if len(text) < 12:
            return False
        if len(text.split()) < 3:
            return False
        unclear_markers = ["tbd", "something", "stuff", "maybe", "later", "follow up"]
        return not any(marker in text for marker in unclear_markers)

    @staticmethod
    def _extract_adf_text(description: JiraTicketsBatch.JiraADFDocument) -> str:
        parts = []
        for block in description.content:
            for chunk in block.get("content", []):
                value = chunk.get("text")
                if value:
                    parts.append(str(value))
        return " ".join(parts).strip()

    def _recompute_reasons(
        self,
        payload: JiraTicketsBatch.JiraCreateIssuePayload,
        existing_reasons: list[str],
    ) -> list[str]:
        # Keep human reviewer annotations while recalculating structural draft reasons.
        preserved = [
            reason
            for reason in existing_reasons
            if reason.startswith("human_note:")
        ]

        dynamic_reasons: list[str] = []
        if payload.fields.assignee is None:
            dynamic_reasons.append("assignee_not_found")
        if payload.fields.duedate is None:
            dynamic_reasons.append("due_date_missing")
        if payload.fields.priority is None:
            dynamic_reasons.append("priority_missing")

        description_text = self._extract_adf_text(payload.fields.description)
        if not self._is_task_clear(payload.fields.summary, description_text):
            dynamic_reasons.append("task_not_clear")

        merged: list[str] = []
        for reason in preserved + dynamic_reasons:
            if reason not in merged:
                merged.append(reason)
        return merged


__all__ = ["ReviewAgent"]

