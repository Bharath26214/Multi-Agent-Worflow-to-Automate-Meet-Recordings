from __future__ import annotations

import json
import os
import base64
from typing import Any, Dict, List, Optional

import requests

from core.models import ExtractedTask, JiraTicketsBatch

class JiraBuilderAgent:
    """Agent that maps extracted tasks into Jira create-issue payloads."""

    def __init__(self, project_key: Optional[str] = None):
        self.project_key = project_key or os.getenv("JIRA_PROJECT_KEY", "KAN")

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
    def _looks_like_bug(description: str) -> bool:
        d = description.lower()
        return any(
            k in d
            for k in ["bug", "error", "exception", "fix", "login", "throws"]
        )

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

        for name in task.assigned_to:
            key = str(name).lower()
            if key in mapping:
                return mapping[key]

        key = str(task.assigned_by).lower()
        return mapping.get(key)

    def build_jira_tickets_batch(
        self,
        tasks: List[ExtractedTask],
        project_key: Optional[str] = None,
    ) -> JiraTicketsBatch:
        project_key = project_key or self.project_key

        payload_models: List[JiraTicketsBatch.JiraCreateIssuePayload] = []
        for task in tasks:
            labels: List[str] = [
                "created-by-meet-agent",
                f"event_id:{task.event_id}",
            ]

            if task.assigned_to and not self._resolve_assignee_account_id(task):
                labels.append(
                    f"unresolved-assignee:{str(task.assigned_to[0]).lower()}"
                )

            priority_obj = (
                JiraTicketsBatch.JiraPriority(name=task.priority.title())
                if task.priority
                else None
            )

            issuetype_obj = JiraTicketsBatch.JiraIssueType(
                name="Bug" if self._looks_like_bug(task.description) else "Task"
            )

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

            account_id = self._resolve_assignee_account_id(task)
            if account_id:
                fields["assignee"] = JiraTicketsBatch.JiraAssignee(
                    accountId=account_id
                )

            payload_models.append(
                JiraTicketsBatch.JiraCreateIssuePayload(
                    fields=JiraTicketsBatch.JiraIssueFields.model_validate(fields)
                )
            )

        return JiraTicketsBatch(tickets=payload_models)

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



