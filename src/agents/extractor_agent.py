from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from ics import Attendee, Calendar, Event
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from core.models import ExtractedTask, ExtractorOutput
from dotenv import load_dotenv
from tools.date_parser import parse_due_date_from_text
from utils.logger import get_logger

load_dotenv()
logger = get_logger(__name__)

SYSTEM_PROMPT = """You are an expert meeting transcript extraction agent.
Extract ONLY actionable items from the transcript as either:
- type="task"
- type="meet" (for scheduling/setup of future meetings or standups)

Rules:
1) Return only actionable type="task" or type="meet" items.
2) Ignore greetings, status updates, and generic chat.
3) If task ownership is unclear, do not invent names. Keep assigned_to empty.
4) Use lowercase priorities: high | medium | low when explicitly implied or stated. if not mentioned, Analyse the due_date and set the priority accordingly. If the due_date is not mentioned, set the priority to low.
5) due_date should be set when timeline is mentioned. If exact date is unknown, keep the spoken phrase (e.g., "next monday") and downstream date parser will normalize it.
6) t0 and t1 should represent approximate seconds from transcript markers.
7) Keep descriptions concise and action-oriented.
8) created_by must be "meet-agent".
9) Confidence should be 0-1 and reflect extractability quality.
10) For type="meet", set meeting_date (ISO datetime) and time_zone when available.

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
        self.ics_output_dir = Path(os.getenv("ICS_OUTPUT_DIR", "demo/generated_meetings"))

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

    @staticmethod
    def _safe_email_from_name(name: str) -> str:
        local = re.sub(r"[^a-z0-9]+", ".", name.strip().lower()).strip(".")
        if not local:
            local = f"attendee.{uuid4().hex[:8]}"
        return f"{local}@meet-agent.local"

    @staticmethod
    def _extract_participants(meeting_transcript_text: str) -> list[str]:
        participants: list[str] = []
        seen = set()
        for line in meeting_transcript_text.splitlines():
            m = re.match(r"^\[\d{2}:\d{2}\]\s+([A-Za-z][A-Za-z0-9_-]{1,40})\s*:", line.strip())
            if not m:
                continue
            name = m.group(1).strip()
            key = name.lower()
            if key not in seen:
                seen.add(key)
                participants.append(name)
        return participants

    @staticmethod
    def _parse_time_from_text(text: str) -> tuple[int, int]:
        m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text, flags=re.IGNORECASE)
        if not m:
            return 10, 0
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        meridian = m.group(3).lower()
        if meridian == "pm" and hour != 12:
            hour += 12
        if meridian == "am" and hour == 12:
            hour = 0
        return hour, minute

    @staticmethod
    def _parse_timezone_from_text(text: str) -> str:
        tz_match = re.search(r"\b(UTC|IST|PST|EST|CST|MST)\b", text, flags=re.IGNORECASE)
        return tz_match.group(1).upper() if tz_match else "UTC"

    def _extract_meeting_lines(self, meeting_transcript_text: str) -> list[str]:
        patterns = [
            r"\bfollow[- ]?up meeting\b",
            r"\bstandup\b",
            r"\bschedule\b.*\bmeeting\b",
            r"\bmeeting\b.*\b(next|tomorrow|today|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
            r"\bset up\b.*\bmeeting\b",
            r"\bsetup\b.*\bstandup\b",
        ]
        compiled = [re.compile(p, flags=re.IGNORECASE) for p in patterns]
        lines = []
        for line in meeting_transcript_text.splitlines():
            raw = line.strip()
            if any(c.search(raw) for c in compiled):
                lines.append(raw)
        return lines

    def _resolve_meeting_start_datetime(
        self,
        event_item: ExtractedTask,
        now: datetime,
    ) -> datetime:
        """
        Resolve meeting start datetime safely.
        Guard against stale LLM dates (e.g., 2023) by recomputing from
        description when needed and never scheduling in the past by default.
        """
        line = event_item.description
        hour, minute = self._parse_time_from_text(line)
        start_dt: datetime | None = None

        if event_item.meeting_date:
            dt_raw = event_item.meeting_date.strip().replace("Z", "+00:00")
            try:
                parsed = datetime.fromisoformat(dt_raw)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                start_dt = parsed
            except ValueError:
                start_dt = None

        if start_dt is None:
            date_iso = parse_due_date_from_text(line)
            if date_iso:
                start_dt = datetime.fromisoformat(
                    f"{date_iso}T{hour:02d}:{minute:02d}:00+00:00"
                )

        # Final fallback when no date phrase exists in text.
        if start_dt is None:
            fallback_date = (now + timedelta(days=1)).date().isoformat()
            start_dt = datetime.fromisoformat(
                f"{fallback_date}T{hour:02d}:{minute:02d}:00+00:00"
            )

        # If LLM returned stale/past date, prefer transcript phrase if possible.
        if start_dt < now:
            date_iso = parse_due_date_from_text(line)
            if date_iso:
                candidate = datetime.fromisoformat(
                    f"{date_iso}T{hour:02d}:{minute:02d}:00+00:00"
                )
                if candidate >= now:
                    start_dt = candidate

        # Hard guarantee: do not generate past meeting invites.
        if start_dt < now:
            next_day = (now + timedelta(days=1)).date().isoformat()
            start_dt = datetime.fromisoformat(
                f"{next_day}T{hour:02d}:{minute:02d}:00+00:00"
            )

        return start_dt

    def _create_meeting_ics_files_from_events(self, events: list[ExtractedTask]) -> list[Path]:
        meeting_events = [e for e in events if e.type == "meet"]
        if not meeting_events:
            return []

        self.ics_output_dir.mkdir(parents=True, exist_ok=True)
        participants = []
        seen = set()
        for event in meeting_events:
            names = [event.spoken_by] + event.spoken_to + event.assigned_to
            for name in names:
                key = (name or "").strip().lower()
                if key and key not in seen:
                    seen.add(key)
                    participants.append(name.strip())
        organizer = participants[0] if participants else "Organizer"
        now = datetime.now(timezone.utc)

        written: list[Path] = []
        for idx, event_item in enumerate(meeting_events, start=1):
            line = event_item.description
            timezone_label = event_item.time_zone or self._parse_timezone_from_text(line)
            start_dt = self._resolve_meeting_start_datetime(event_item, now)

            end_dt = start_dt + timedelta(hours=1)

            cal = Calendar()
            event = Event()
            event.name = "Follow-up Meeting"
            event.begin = start_dt
            event.end = end_dt
            event.description = line
            event.location = f"Timezone: {timezone_label}"
            event.organizer = organizer
            for person in participants:
                event.add_attendee(
                    Attendee(
                        email=self._safe_email_from_name(person),
                        common_name=person,
                    )
                )
            cal.events.add(event)

            out_path = self.ics_output_dir / f"future_meeting_{idx:02d}.ics"
            out_path.write_text(cal.serialize(), encoding="utf-8")
            written.append(out_path)
        return written

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
            if task.type == "task":
                task = self._normalize_due_date(task)
                task = self._infer_assignee_from_transcript(task, meeting_transcript_text)
            logger.info(
                "Post-process event %s (%s) | desc='%s' | assigned_to=%s | spoken_to=%s | due_date=%s | meeting_date=%s",
                task.event_id,
                task.type,
                task.description,
                task.assigned_to,
                task.spoken_to,
                task.due_date,
                task.meeting_date,
            )
            normalized_tasks.append(task)
        result.tasks = normalized_tasks
        generated_ics = self._create_meeting_ics_files_from_events(result.tasks)
        if generated_ics:
            logger.info(
                "Generated %d meeting .ics file(s): %s",
                len(generated_ics),
                ", ".join(str(p) for p in generated_ics),
            )
        logger.info("Extraction complete: %d task(s) found", len(result.tasks))
        return result


__all__ = ["ExtractorAgent"]

