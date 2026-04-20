from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from langchain_core.tools import tool

WEEKDAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def _reference_date(reference: Optional[date] = None) -> date:
    return reference or datetime.now(timezone.utc).date()


def _to_iso(d: date) -> str:
    return d.isoformat()


def _parse_relative_weekday(text: str, ref: date) -> Optional[str]:
    match = re.search(
        r"\b(next|this)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    qualifier = match.group(1).lower()
    target_name = match.group(2).lower()
    target_idx = WEEKDAY_INDEX[target_name]
    current_idx = ref.weekday()

    days_ahead = (target_idx - current_idx) % 7
    if qualifier == "next":
        # Interpret "next Monday" as the upcoming Monday; if today is Monday, use +7 days.
        days_ahead = days_ahead if days_ahead != 0 else 7
    elif qualifier == "this" and days_ahead == 0:
        days_ahead = 0

    resolved = ref + timedelta(days=days_ahead)
    return _to_iso(resolved)


def _parse_next_week_weekday(text: str, ref: date) -> Optional[str]:
    match = re.search(
        r"\bnext\s+week\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    target_name = match.group(1).lower()
    target_idx = WEEKDAY_INDEX[target_name]
    # Start from same weekday in next week window.
    start_next_week = ref + timedelta(days=(7 - ref.weekday()))
    offset = (target_idx - start_next_week.weekday()) % 7
    resolved = start_next_week + timedelta(days=offset)
    return _to_iso(resolved)


def _parse_in_days(text: str, ref: date) -> Optional[str]:
    match = re.search(r"\bin\s+(\d+)\s+day(?:s)?\b", text, flags=re.IGNORECASE)
    if not match:
        return None
    days = int(match.group(1))
    return _to_iso(ref + timedelta(days=days))


def _parse_end_of_week(text: str, ref: date) -> Optional[str]:
    if not re.search(r"\b(end of week|eow)\b", text, flags=re.IGNORECASE):
        return None
    # Friday as end-of-work-week.
    friday_idx = WEEKDAY_INDEX["friday"]
    days_ahead = (friday_idx - ref.weekday()) % 7
    return _to_iso(ref + timedelta(days=days_ahead))


def _parse_eod(text: str, ref: date) -> Optional[str]:
    if not re.search(r"\beod\b", text, flags=re.IGNORECASE):
        return None
    if re.search(r"\btomorrow\b", text, flags=re.IGNORECASE):
        return _to_iso(ref + timedelta(days=1))
    if re.search(r"\b(next|this)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", text, flags=re.IGNORECASE):
        return _parse_relative_weekday(text, ref)
    return _to_iso(ref)


def parse_due_date_from_text(
    text: str,
    reference: Optional[date] = None,
) -> Optional[str]:
    """
    Parse due date from absolute or relative text and return YYYY-MM-DD.
    Supports:
    - Explicit YYYY-MM-DD
    - today / tomorrow
    - next <weekday> / this <weekday>
    """
    if not text:
        return None
    ref = _reference_date(reference)

    absolute = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if absolute:
        return absolute.group(1)

    lowered = text.lower()
    if "tomorrow" in lowered:
        return _to_iso(ref + timedelta(days=1))
    if "today" in lowered:
        return _to_iso(ref)

    next_week = _parse_next_week_weekday(text, ref)
    if next_week:
        return next_week

    rel = _parse_relative_weekday(text, ref)
    if rel:
        return rel

    in_days = _parse_in_days(text, ref)
    if in_days:
        return in_days

    eow = _parse_end_of_week(text, ref)
    if eow:
        return eow

    eod = _parse_eod(text, ref)
    if eod:
        return eod

    return None


@tool("date_parser")
def date_parser_tool(text: str) -> str:
    """
    Parse relative/absolute date phrases into YYYY-MM-DD.
    Returns empty string when no date could be parsed.
    """
    parsed = parse_due_date_from_text(text)
    return parsed or ""

