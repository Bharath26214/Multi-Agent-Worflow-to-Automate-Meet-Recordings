from __future__ import annotations

import sys
from pathlib import Path

# Make `src/` importable as top-level modules (agents/, core/, graph/, ...)
SRC_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC_ROOT))

from graph.workflow import build_graph  # noqa: E402
from core.models import JiraTicketsBatch  # noqa: E402
from agents.jira_builder_agent import JiraBuilderAgent  # noqa: E402
from utils.logger import get_logger  # noqa: E402

logger = get_logger(__name__)


def run_demo(raw_recording_text: str):
    logger.info("Planning workflow")
    graph = build_graph()
    initial_state = {
        "raw_recording_text": raw_recording_text,
        "extracted_tasks": [],
        "jira_tickets_batch": JiraTicketsBatch(tickets=[]),
        "draft_tickets": [],
        "draft_tickets_for_review": [],
        "approved_draft_tickets_batch": JiraTicketsBatch(tickets=[]),
        "rejected_draft_tickets": [],
        "review_index": 0,
        "current_draft_ticket": None,
        "review_action": "",
        "review_edit_prompt": "",
    }
    final_state = graph.invoke(initial_state)
    logger.info(
        "Graph complete: %d extracted task(s), %d ready ticket(s), %d draft ticket(s)",
        len(final_state["extracted_tasks"]),
        len(final_state["jira_tickets_batch"].tickets),
        len(final_state["draft_tickets"]),
    )
    output = {
        "extracted_tasks": final_state["extracted_tasks"],
        "ready_jira_tickets_batch": final_state["jira_tickets_batch"],
        "draft_tickets": final_state["draft_tickets"],
    }
    jira_builder = JiraBuilderAgent()
    output["approved_draft_tickets_batch"] = final_state["approved_draft_tickets_batch"]
    output["rejected_draft_tickets"] = final_state["rejected_draft_tickets"]

    combined_tickets = (
        final_state["jira_tickets_batch"].tickets
        + final_state["approved_draft_tickets_batch"].tickets
    )
    final_batch_to_raise = JiraTicketsBatch(tickets=combined_tickets)
    output["final_batch_to_raise"] = final_batch_to_raise
    logger.info(
        "Creating Jira issues for %d final approved ticket(s)",
        len(final_batch_to_raise.tickets),
    )
    output["jira_create_results"] = jira_builder.create_jira_issues(final_batch_to_raise)
    logger.info("Workflow finished")
    return output


if __name__ == "__main__":
    sample_transcript = """
[00:00] John: Good morning everyone. Quick status sync for checkout and release prep.
[00:10] Priya: Auth middleware tests are done from QA side.
[00:22] John: Alex, finish Stripe API integration by 2026-04-20. High priority.
[00:36] Alex: Confirmed, I’ll deliver by 2026-04-20.
[00:48] John: Priya, create end-to-end checkout test cases by 2026-04-21. Medium priority.
[01:02] Priya: Sure, I'll take that.
[01:15] Mike: We still see webhook retries failing intermittently.
[01:24] John: Mike, fix the webhook retry bug and push patch by 2026-04-19. High priority.
[01:40] Mike: Done, I’ll handle it.
[01:52] John: Someone update the API docs soon.
[02:03] John: Let's also improve monitoring maybe this week.
[02:14] John: Alex, cleanup payment endpoint docs by 2026-04-22. Low priority.
[02:27] Alex: Got it.
[02:35] John: Great, let's close.
""".strip()

    out = run_demo(sample_transcript)
    # Keep only Jira create responses as terminal output.
    print(out["jira_create_results"])
