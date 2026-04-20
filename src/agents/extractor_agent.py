from __future__ import annotations

import os
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from core.models import ExtractedTask, ExtractorOutput
from dotenv import load_dotenv
from tools.date_parser import parse_due_date_from_text
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
5) due_date should be set when timeline is mentioned. If exact date is unknown, keep the spoken phrase (e.g., "next monday") and downstream date parser will normalize it.
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

    @staticmethod
    def _normalize_due_date(task: ExtractedTask) -> ExtractedTask:
        candidates = [task.due_date or "", task.description]
        for value in candidates:
            parsed = parse_due_date_from_text(value)
            if parsed:
                task.due_date = parsed
                break
        return task

    @staticmethod
    def _tokenize(value: str) -> set[str]:
        return {w for w in re.findall(r"[a-z0-9]+", value.lower()) if len(w) > 2}

    def _infer_assignee_from_transcript(
        self,
        task: ExtractedTask,
        meeting_transcript_text: str,
    ) -> ExtractedTask:
        if task.assigned_to:
            return task

        task_tokens = self._tokenize(task.description)
        if not task_tokens:
            return task

        pattern = re.compile(
            r"^\[(?P<ts>\d{2}:\d{2})\]\s+(?P<speaker>[A-Za-z][A-Za-z0-9_-]{1,40}):\s+"
            r"(?P<addressee>[A-Za-z][A-Za-z0-9_-]{1,40})\s*,\s*(?P<utterance>.+)$"
        )

        best_name = None
        best_score = 0.0
        for line in meeting_transcript_text.splitlines():
            m = pattern.match(line.strip())
            if not m:
                continue
            utterance_tokens = self._tokenize(m.group("utterance"))
            if not utterance_tokens:
                continue
            overlap = len(task_tokens.intersection(utterance_tokens))
            if overlap == 0:
                continue
            score = overlap / max(1, len(task_tokens))
            if score > best_score:
                best_score = score
                best_name = m.group("addressee")

        if best_name and best_score >= 0.3:
            task.assigned_to = [best_name]
            if not task.spoken_to:
                task.spoken_to = [best_name]
        return task

    def extract_tasks_from_text(self, meeting_transcript_text: str) -> ExtractorOutput:
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
            f"{meeting_transcript_text}\n\n"
            "Return extracted tasks as structured output."
        )

        result = structured_llm.invoke(
            [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=user_prompt)]
        )
        normalized_tasks = []
        for task in result.tasks:
            task = self._normalize_due_date(task)
            task = self._infer_assignee_from_transcript(task, meeting_transcript_text)
            logger.info(
                "Post-process task %s | desc='%s' | assigned_to=%s | spoken_to=%s | due_date=%s",
                task.event_id,
                task.description,
                task.assigned_to,
                task.spoken_to,
                task.due_date,
            )
            normalized_tasks.append(task)
        result.tasks = normalized_tasks
        logger.info("Extraction complete: %d task(s) found", len(result.tasks))
        return result


__all__ = ["ExtractorAgent"]

