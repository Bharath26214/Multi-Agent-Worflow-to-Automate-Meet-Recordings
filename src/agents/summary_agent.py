from __future__ import annotations

import os

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from core.models import MeetingSummary
from utils.logger import get_logger

logger = get_logger(__name__)

SYSTEM_PROMPT = """You are an expert meeting summarization assistant.
Generate a structured meeting summary from transcript text.

Rules:
1) Summarize the ENTIRE meeting, not just tasks.
2) Keep summary concise and factual.
3) Capture participants/attendees, organizer, meeting date/timestamp if available.
4) Capture action items, future meetings, deadlines, decisions, risks, and next steps.
5) Include non-task context too (status updates, blockers, meeting plans, commitments).
6) Do not fabricate facts not present in transcript.
"""


class SummaryAgent:
    """LLM-backed agent for full meeting summary generation."""

    def __init__(self, model: str | None = None):
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    def summarize(self, raw_recording_text: str) -> MeetingSummary:
        logger.info("Generating meeting summary")
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Please export it before running."
            )

        llm = ChatOpenAI(model=self.model, temperature=0)
        structured_llm = llm.with_structured_output(MeetingSummary)
        result = structured_llm.invoke(
            [
                SystemMessage(content=SYSTEM_PROMPT),
                HumanMessage(
                    content=(
                        "Transcript:\n"
                        f"{raw_recording_text}\n\n"
                        "Return a structured meeting summary across the full meeting context."
                    )
                ),
            ]
        )
        print(result)
        logger.info("Meeting summary generated")
        return result


__all__ = ["SummaryAgent"]

