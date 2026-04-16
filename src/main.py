from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make `src/` importable as top-level modules (agents/, core/, graph/, ...)
SRC_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC_ROOT))

from graph.workflow import build_graph  # noqa: E402
from core.models import JiraTicketsBatch  # noqa: E402
from agents.jira_builder_agent import JiraBuilderAgent  # noqa: E402


def run_demo(raw_recording_text: str):
    graph = build_graph()
    initial_state = {
        "raw_recording_text": raw_recording_text,
        "extracted_tasks": [],
        "jira_tickets_batch": JiraTicketsBatch(tickets=[]),
    }
    final_state = graph.invoke(initial_state)
    output = {
        "extracted_tasks": final_state["extracted_tasks"],
        "jira_tickets_batch": final_state["jira_tickets_batch"],
    }
    jira_builder = JiraBuilderAgent()
    # Use graph-generated batch directly to avoid rebuilding tickets.
    output["jira_create_results"] = jira_builder.create_jira_issues(
        final_state["jira_tickets_batch"]
    )
    return output


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
    out = run_demo(sample_transcript)
    payload = {
        "run_started_at": started_at,
        "task_count": len(out["extracted_tasks"]),
        "jira_ticket_count": len(out["jira_tickets_batch"].tickets),
        "extracted_tasks": out["extracted_tasks"],
        "jira_tickets_batch": out["jira_tickets_batch"].model_dump(),
    }
    payload["jira_create_results"] = out["jira_create_results"]
    print(json.dumps(payload, indent=2))
