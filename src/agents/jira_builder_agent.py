from __future__ import annotations

import json
import os
import base64
import re
from difflib import get_close_matches
from typing import Any, Dict, List, Optional

import requests

from core.models import DraftJiraTicket, ExtractedTask, JiraReviewQueue, JiraTicketsBatch
from utils.logger import get_logger

logger = get_logger(__name__)

class JiraBuilderAgent:
    """Agent that maps extracted tasks into Jira create-issue payloads."""

    def __init__(self, project_key: Optional[str] = None):
        self.project_key = project_key or os.getenv("JIRA_PROJECT_KEY", "KAN")
        self.min_confidence = float(os.getenv("JIRA_MIN_CONFIDENCE", "0.75"))

    @staticmethod
    def _build_adf_description(task: ExtractedTask) -> Dict[str, Any]:
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

    @staticmethod
    def _load_assignee_accountid_map() -> Dict[str, str]:
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

    @classmethod
    def _resolve_assignee_account_id(cls, task: ExtractedTask) -> Optional[str]:
        mapping = cls._load_assignee_accountid_map()
        if not mapping:
            return None

        # 1) Prefer explicit extracted assignees.
        for name in task.assigned_to:
            key = str(name).lower()
            if key in mapping:
                return mapping[key]
            close = get_close_matches(key, list(mapping.keys()), n=1, cutoff=0.8)
            if close:
                return mapping[close[0]]

        # 1.5) Fallback to addressee list if extractor captured spoken_to better than assigned_to.
        for name in task.spoken_to:
            key = str(name).lower()
            if key in mapping:
                return mapping[key]
            close = get_close_matches(key, list(mapping.keys()), n=1, cutoff=0.8)
            if close:
                return mapping[close[0]]

        # 2) Infer from description patterns like "Alex to finish ..." or "Alex, finish ...".
        desc = task.description.strip().lower()
        starts_with_name = re.match(r"^([a-z][a-z0-9_-]{1,30})\s*(?:to|,)\b", desc)
        if starts_with_name:
            inferred = starts_with_name.group(1)
            if inferred in mapping:
                return mapping[inferred]
            close = get_close_matches(inferred, list(mapping.keys()), n=1, cutoff=0.8)
            if close:
                return mapping[close[0]]

        # 3) If any mapped user name appears as a whole word in description, use it.
        for name_key, account_id in mapping.items():
            if re.search(rf"\b{re.escape(name_key)}\b", desc):
                return account_id

        return None

    @staticmethod
    def _is_task_clear(task: ExtractedTask) -> bool:
        description = task.description.strip().lower()
        if len(description) < 12:
            return False
        if len(description.split()) < 3:
            return False
        unclear_markers = ["tbd", "something", "stuff", "maybe", "later", "follow up"]
        return not any(marker in description for marker in unclear_markers)

    def _draft_reasons(
        self,
        task: ExtractedTask,
        assignee_account_id: Optional[str],
    ) -> List[str]:
        reasons: List[str] = []
        if not assignee_account_id:
            reasons.append("assignee_not_found")
        if not task.due_date:
            reasons.append("due_date_missing")
        if not task.priority:
            reasons.append("priority_missing")
        if not self._is_task_clear(task):
            reasons.append("task_not_clear")
        return reasons

    def build_jira_tickets_batch(
        self,
        tasks: List[ExtractedTask],
        project_key: Optional[str] = None,
    ) -> JiraTicketsBatch:
        review_queue = self.build_jira_review_queue(tasks, project_key=project_key)
        return review_queue.ready_batch

    def build_jira_review_queue(
        self,
        tasks: List[ExtractedTask],
        project_key: Optional[str] = None,
    ) -> JiraReviewQueue:
        logger.info("Building Jira payloads and draft review queue")
        project_key = project_key or self.project_key

        ready_payloads: List[JiraTicketsBatch.JiraCreateIssuePayload] = []
        draft_tickets: List[DraftJiraTicket] = []
        for task in tasks:
            labels: List[str] = [
                "created-by-meet-agent",
                f"event_id:{task.event_id}",
            ]

            assignee_account_id = self._resolve_assignee_account_id(task)
            if task.assigned_to and not assignee_account_id:
                labels.append(
                    f"unresolved-assignee:{str(task.assigned_to[0]).lower()}"
                )

            priority_obj = (
                JiraTicketsBatch.JiraPriority(name=task.priority.title())
                if task.priority
                else None
            )

            issuetype_obj = JiraTicketsBatch.JiraIssueType(name="Task")

            description_obj = JiraTicketsBatch.JiraADFDocument(
                **self._build_adf_description(task)
            )

            fields: Dict[str, Any] = {
                "project": JiraTicketsBatch.JiraProject(key=project_key),
                "summary": task.description.strip(),
                "description": description_obj,
                "issuetype": issuetype_obj,
                "labels": labels,
            }

            if task.due_date:
                fields["duedate"] = task.due_date
            if priority_obj:
                fields["priority"] = priority_obj

            if assignee_account_id:
                fields["assignee"] = JiraTicketsBatch.JiraAssignee(
                    accountId=assignee_account_id
                )

            payload_model = JiraTicketsBatch.JiraCreateIssuePayload(
                fields=JiraTicketsBatch.JiraIssueFields.model_validate(fields)
            )
            reasons = self._draft_reasons(task, assignee_account_id)
            if reasons:
                draft_tickets.append(
                    DraftJiraTicket(
                        event_id=task.event_id,
                        summary=task.description.strip(),
                        reasons=reasons,
                        payload=payload_model,
                    )
                )
            else:
                ready_payloads.append(payload_model)

        return JiraReviewQueue(
            ready_batch=JiraTicketsBatch(tickets=ready_payloads),
            draft_tickets=draft_tickets,
        )

    @staticmethod
    def _jira_request_headers() -> Dict[str, str]:
        email = os.getenv("JIRA_EMAIL")
        api_token = os.getenv("JIRA_API_KEY")
        if not email or not api_token:
            raise RuntimeError(
                "JIRA_EMAIL and JIRA_API_KEY must be set to create Jira issues."
            )
        auth_str = f"{email}:{api_token}"
        b64_auth = base64.b64encode(auth_str.encode()).decode()
        return {
            "Authorization": f"Basic {b64_auth}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def create_jira_issues(self, batch: JiraTicketsBatch) -> List[Dict[str, Any]]:
        """
        Create Jira issues from an already-built batch payload.
        Returns per-ticket status and response payload text/json.
        """
        logger.info("Creating Jira tickets: %d ticket(s)", len(batch.tickets))
        domain = os.getenv("JIRA_DOMAIN")
        if not domain:
            raise RuntimeError("JIRA_DOMAIN must be set to create Jira issues.")

        url = f"https://{domain}/rest/api/3/issue"
        headers = self._jira_request_headers()

        results: List[Dict[str, Any]] = []
        for ticket in batch.tickets:
            payload = ticket.model_dump()
            response = requests.post(url, headers=headers, json=payload)
            item: Dict[str, Any] = {"status_code": response.status_code}
            try:
                item["response"] = response.json()
            except ValueError:
                item["response"] = response.text
            results.append(item)
        success_count = sum(1 for x in results if int(x["status_code"]) in {200, 201})
        logger.info(
            "Jira create complete: %d success, %d failed",
            success_count,
            len(results) - success_count,
        )
        return results

    def build_and_create_jira_issues(
        self,
        tasks: List[ExtractedTask],
        project_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Convenience method: build Jira payloads from tasks and create issues.
        """
        batch = self.build_jira_tickets_batch(tasks, project_key=project_key)
        results = self.create_jira_issues(batch)
        return {
            "jira_tickets_batch": batch,
            "jira_create_results": results,
        }


__all__ = ["JiraBuilderAgent"]



