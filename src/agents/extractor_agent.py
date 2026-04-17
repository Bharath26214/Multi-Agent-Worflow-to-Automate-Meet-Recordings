from __future__ import annotations

import os

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from core.models import ExtractorOutput
from dotenv import load_dotenv
from utils.logger import get_logger

load_dotenv()
logger = get_logger(__name__)

SYSTEM_PROMPT = """You are an expert meeting transcript extraction agent.
Extract ONLY actionable task items from the transcript.

Rules:
1) Return only type="task" items.
2) Ignore greetings, status updates, and generic chat.
3) If task ownership is unclear, do not invent names. Keep assigned_to empty.
4) Use lowercase priorities: high | medium | low when explicitly implied or stated. if not mentioned, Analyse the due_date and set the priority accordingly. If the due_date is not mentioned, set the priority to low.
5) due_date must be YYYY-MM-DD when available, else null.
6) t0 and t1 should represent approximate seconds from transcript markers.
7) Keep descriptions concise and action-oriented.
8) created_by must be "meet-agent".
9) Confidence should be 0-1 and reflect extractability quality.

Draft-routing alignment (important):
- If task is ambiguous/vague/underspecified, set LOWER confidence.
- If assignee is unclear or missing, keep assigned_to empty and set LOWER confidence.
- If timeline/intent is uncertain, set LOWER confidence.
- Use higher confidence only when task, assignee, and intent are explicit in transcript.
"""

class ExtractorAgent:
    """LLM-backed agent that extracts only `type="task"` events."""

    def __init__(self, model: str | None = None):
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    def extract_tasks_from_text(self, raw_recording_text: str) -> ExtractorOutput:
        logger.info("Extracting tasks from meeting transcript")
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Please export it before running."
            )

        llm = ChatOpenAI(model=self.model, temperature=0)
        structured_llm = llm.with_structured_output(ExtractorOutput)

        user_prompt = (
            "Transcript:\n"
            f"{raw_recording_text}\n\n"
            "Return extracted tasks as structured output."
        )

        result = structured_llm.invoke(
            [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=user_prompt)]
        )
        logger.info("Extraction complete: %d task(s) found", len(result.tasks))
        return result


__all__ = ["ExtractorAgent"]

