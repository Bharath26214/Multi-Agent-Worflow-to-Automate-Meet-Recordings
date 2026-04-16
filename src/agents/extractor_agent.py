from __future__ import annotations

import os

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from core.models import ExtractorOutput
from dotenv import load_dotenv
load_dotenv()

SYSTEM_PROMPT = """You are an expert meeting transcript extraction agent.
Extract ONLY actionable task items from the transcript.

Rules:
1) Return only type="task" items.
2) Ignore greetings, status updates, and generic chat.
3) If task ownership is unclear, do not invent names.
4) Use lowercase priorities: high | medium | low when explicitly implied or stated.
5) due_date must be YYYY-MM-DD when available, else null.
6) t0 and t1 should represent approximate seconds from transcript markers.
7) Keep descriptions concise and action-oriented.
8) created_by must be "meet-agent".
9) Confidence should be 0-1.
"""

class ExtractorAgent:
    """LLM-backed agent that extracts only `type="task"` events."""

    def __init__(self, model: str | None = None):
        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    def extract_tasks_from_text(self, raw_recording_text: str) -> ExtractorOutput:
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

        return structured_llm.invoke(
            [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=user_prompt)]
        )


__all__ = ["ExtractorAgent"]

