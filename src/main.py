from __future__ import annotations

import sys
from pathlib import Path

# Make `src/` importable as top-level modules (agents/, core/, graph/, ...)
SRC_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC_ROOT))

from graph.workflow import build_graph  # noqa: E402
from core.models import JiraTicketsBatch, MeetingSummary  # noqa: E402
from utils.logger import get_logger  # noqa: E402

logger = get_logger(__name__)


def run_demo(recording_file_path: str):
    logger.info("Planning workflow")
    graph = build_graph()
    initial_state = {
        "recording_file_path": recording_file_path,
        "meeting_transcript_text": "",
        "diarized_transcript_text": "",
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
        "jira_create_results": [],
        "meeting_summary": MeetingSummary(
            summary="",
            key_points=[],
            decisions=[],
            risks=[],
            next_steps=[],
        ),
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
        "meeting_summary": final_state["meeting_summary"],
    }
    output["approved_draft_tickets_batch"] = final_state["approved_draft_tickets_batch"]
    output["rejected_draft_tickets"] = final_state["rejected_draft_tickets"]

    combined_tickets = (
        final_state["jira_tickets_batch"].tickets
        + final_state["approved_draft_tickets_batch"].tickets
    )
    final_batch_to_raise = JiraTicketsBatch(tickets=combined_tickets)
    output["final_batch_to_raise"] = final_batch_to_raise
    output["jira_create_results"] = final_state["jira_create_results"]
    logger.info("Workflow finished")
    return output


if __name__ == "__main__":
    recordings_dir = SRC_ROOT / "recordings"
    preferred_audio = recordings_dir / "meet_recording.wav"
    fallback_audio = recordings_dir / "meeting_audio.wav"
    recording_file_path = (
        preferred_audio if preferred_audio.exists() else fallback_audio
    )
    if not recording_file_path.exists():
        raise FileNotFoundError(
            "No recording file found. Expected recordings/meet_recording.wav "
            "or recordings/meeting_audio.wav"
        )
    out = run_demo(str(recording_file_path))
    # Keep only Jira create responses as terminal output.
    print(out["meeting_summary"])
    print(out["jira_create_results"])
