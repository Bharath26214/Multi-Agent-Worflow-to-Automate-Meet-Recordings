from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

SRC_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SRC_ROOT))

from agents.extractor_agent import ExtractorAgent  # noqa: E402
from agents.jira_builder_agent import JiraBuilderAgent  # noqa: E402
from agents.summary_agent import SummaryAgent  # noqa: E402
from agents.transcriber_agent import TranscriberAgent  # noqa: E402
from core.models import ExtractedTask  # noqa: E402
from utils.logger import get_logger  # noqa: E402

logger = get_logger(__name__)

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
DEFAULT_OUTPUT_DIR = SRC_ROOT / "output"
DEFAULT_STATE_PATH = DEFAULT_OUTPUT_DIR / "drive_processed_files.json"
DEFAULT_REPORT_DIR = DEFAULT_OUTPUT_DIR / "drive_runs"


def _extract_folder_id(folder_ref: str) -> str:
    value = folder_ref.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{10,}", value):
        return value
    match = re.search(r"/folders/([A-Za-z0-9_-]+)", value)
    if not match:
        raise ValueError(
            "GOOGLE_DRIVE_FOLDER_LINK must be a Drive folder URL or a folder ID."
        )
    return match.group(1)


def _load_json_file(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("State file is invalid JSON, resetting: %s", path)
        return default


def _save_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _build_drive_client(service_account_json: str):
    creds = service_account.Credentials.from_service_account_file(
        service_account_json,
        scopes=DRIVE_SCOPES,
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _list_folder_files(drive, folder_id: str) -> list[dict[str, str]]:
    query = (
        f"'{folder_id}' in parents and trashed = false and "
        "("
        "mimeType = 'text/plain' or "
        "mimeType = 'application/vnd.google-apps.document' or "
        "mimeType contains 'audio/'"
        ")"
    )
    result = (
        drive.files()
        .list(
            q=query,
            orderBy="createdTime asc",
            fields="files(id,name,mimeType,createdTime,modifiedTime,webViewLink)",
            pageSize=200,
        )
        .execute()
    )
    return result.get("files", [])


def _download_transcript_text(drive, file_meta: dict[str, str]) -> str:
    file_id = file_meta["id"]
    mime_type = file_meta.get("mimeType", "")
    if mime_type == "application/vnd.google-apps.document":
        request = drive.files().export_media(fileId=file_id, mimeType="text/plain")
    else:
        request = drive.files().get_media(fileId=file_id)

    from io import BytesIO

    fh = BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return fh.getvalue().decode("utf-8", errors="replace").strip()


def _download_drive_file_bytes(drive, file_id: str) -> bytes:
    request = drive.files().get_media(fileId=file_id)
    from io import BytesIO

    fh = BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return fh.getvalue()


def _looks_like_audio(file_meta: dict[str, str]) -> bool:
    mime_type = (file_meta.get("mimeType") or "").lower()
    name = (file_meta.get("name") or "").lower()
    if mime_type.startswith("audio/"):
        return True
    return name.endswith((".wav", ".mp3", ".m4a", ".mp4", ".aac", ".flac"))


def _extract_transcript_from_drive_file(drive, file_meta: dict[str, str]) -> tuple[str, str]:
    """
    Returns (transcript_text, input_kind) where input_kind is "text" or "audio".
    """
    if _looks_like_audio(file_meta):
        file_id = file_meta["id"]
        file_name = file_meta.get("name") or f"{file_id}.wav"
        suffix = Path(file_name).suffix or ".wav"
        audio_bytes = _download_drive_file_bytes(drive, file_id)
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = Path(tmp.name)
        try:
            transcriber = TranscriberAgent()
            transcript = transcriber.transcribe_audio_file(str(tmp_path))
            return transcript, "audio"
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                logger.warning("Failed to remove temp audio file: %s", tmp_path)

    transcript = _download_transcript_text(drive, file_meta)
    return transcript, "text"


def _process_transcript_text(transcript: str) -> dict[str, Any]:
    extractor = ExtractorAgent()
    summary_agent = SummaryAgent()
    jira_builder = JiraBuilderAgent()

    extracted = extractor.extract_tasks_from_text(transcript)
    events = [ExtractedTask.model_validate(t.model_dump()) for t in extracted.tasks]
    task_events = [e for e in events if e.type == "task"]
    review_queue = jira_builder.build_jira_review_queue(task_events)
    jira_results = []
    if review_queue.ready_batch.tickets:
        jira_results = jira_builder.create_jira_issues(review_queue.ready_batch)
    meeting_summary = summary_agent.summarize(transcript)
    return {
        "extracted_events_count": len(events),
        "task_events_count": len(task_events),
        "ready_tickets_count": len(review_queue.ready_batch.tickets),
        "draft_tickets_count": len(review_queue.draft_tickets),
        "draft_ticket_event_ids": [d.event_id for d in review_queue.draft_tickets],
        "jira_create_results": jira_results,
        "meeting_summary": meeting_summary.model_dump(),
    }


def run_drive_cron_once() -> dict[str, Any]:
    folder_link = os.getenv("GOOGLE_DRIVE_FOLDER_LINK", "").strip()
    service_account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    state_path = Path(os.getenv("DRIVE_CRON_STATE_FILE", str(DEFAULT_STATE_PATH)))
    report_dir = Path(os.getenv("DRIVE_CRON_REPORT_DIR", str(DEFAULT_REPORT_DIR)))

    if not folder_link:
        raise RuntimeError("GOOGLE_DRIVE_FOLDER_LINK is required.")
    if not service_account_json:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is required.")

    folder_id = _extract_folder_id(folder_link)
    drive = _build_drive_client(service_account_json)

    state = _load_json_file(state_path, default={"processed_file_ids": []})
    processed = set(state.get("processed_file_ids", []))

    summary: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "folder_id": folder_id,
        "processed_now": [],
        "skipped_existing": 0,
        "errors": [],
    }

    try:
        files = _list_folder_files(drive, folder_id)
    except HttpError as exc:
        raise RuntimeError(f"Failed to list Drive files: {exc}") from exc

    for meta in files:
        file_id = meta["id"]
        if file_id in processed:
            summary["skipped_existing"] += 1
            continue

        try:
            transcript, input_kind = _extract_transcript_from_drive_file(drive, meta)
            if not transcript:
                raise ValueError(f"{input_kind.title()} file produced empty transcript.")
            result = _process_transcript_text(transcript)
            summary["processed_now"].append(
                {
                    "file_id": file_id,
                    "file_name": meta.get("name"),
                    "input_kind": input_kind,
                    "created_time": meta.get("createdTime"),
                    "web_view_link": meta.get("webViewLink"),
                    "result": result,
                }
            )
            processed.add(file_id)
            logger.info(
                "Processed Drive %s file: %s",
                input_kind,
                meta.get("name"),
            )
        except Exception as exc:  # pragma: no cover
            logger.exception("Failed to process file %s", meta.get("name"))
            summary["errors"].append(
                {
                    "file_id": file_id,
                    "file_name": meta.get("name"),
                    "error": str(exc),
                }
            )

    state["processed_file_ids"] = sorted(processed)
    _save_json_file(state_path, state)

    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    summary["processed_count"] = len(summary["processed_now"])
    summary["error_count"] = len(summary["errors"])

    report_dir.mkdir(parents=True, exist_ok=True)
    report_name = datetime.now(timezone.utc).strftime("run_%Y%m%dT%H%M%SZ.json")
    report_path = report_dir / report_name
    _save_json_file(report_path, summary)
    summary["report_path"] = str(report_path)
    return summary


if __name__ == "__main__":
    run_summary = run_drive_cron_once()
    logger.info(
        "Drive cron run complete | processed=%d | errors=%d | report=%s",
        run_summary["processed_count"],
        run_summary["error_count"],
        run_summary["report_path"],
    )
