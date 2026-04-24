# MeetFlow AI: Recording-to-Jira Automation

Turn meeting recordings or transcripts into:

- structured action items
- Jira tickets (ready + draft review model)
- meeting summaries
- calendar invites (`.ics`) for future meetings

The project supports both one-off local runs and scheduled Google Drive polling for new uploads.

## What This Product Does

1. Ingests meeting input (`.wav` audio or text transcript).
2. Transcribes audio with timestamps and speaker normalization.
3. Extracts actionable events:
  - `task` (Jira candidates)
  - `meet` (future meeting scheduling hints)
4. Builds Jira payloads:
  - raises ready tickets automatically
  - keeps ambiguous tickets as drafts for review paths
5. Generates a full meeting summary in parallel.
6. Creates `.ics` files for future meetings detected in conversation.

## Tech Stack

- Python 3.10+
- Poetry
- LangGraph
- LangChain + OpenAI
- Jira REST API
- Google Drive API (service account) for scheduled ingestion
- `ics` for calendar file generation

## Repository Structure

- `src/main.py` - local workflow entrypoint (audio-first run)
- `src/graph/workflow.py` - LangGraph nodes and routing
- `src/agents/` - extractor, transcriber, summary, Jira builder, review
- `src/services/drive_cron_worker.py` - Drive polling + auto pipeline trigger
- `src/tools/date_parser.py` - date parsing utility for phrases like "next monday"
- `src/output/` - runtime outputs (Drive run reports/state)
- `demo/generated_meetings/` - generated `.ics` files

## Prerequisites

- Python and Poetry installed
- OpenAI API key
- Jira cloud access (domain, email, API token, project key)
- (Optional, for Drive automation) Google Cloud project with Drive API enabled and a service account key JSON

## Installation

```bash
poetry install
```

## Environment Variables

Create `.env` based on `.env.sample` and fill values:

```env
OPENAI_API_KEY=

JIRA_API_KEY=
JIRA_DOMAIN=
JIRA_PROJECT_KEY=
JIRA_EMAIL=

JIRA_ACCOUNT_ID=
JIRA_ASSIGNEE_ACCOUNTID_MAP_JSON=

GOOGLE_DRIVE_FOLDER_LINK=
GOOGLE_SERVICE_ACCOUNT_JSON=
```

Notes:

- `JIRA_ASSIGNEE_ACCOUNTID_MAP_JSON` should map assignee names to Jira accountIds.
- `GOOGLE_DRIVE_FOLDER_LINK` can be either full folder URL or folder ID.
- `GOOGLE_SERVICE_ACCOUNT_JSON` must be an absolute local path to your service-account JSON file.

## Run Locally (Single Recording)

The local flow reads:

- `src/recordings/meet_recording.wav` (preferred), or
- `src/recordings/meeting_audio.wav` (fallback)

Run:

```bash
poetry run python src/main.py
```

Output includes:

- meeting summary
- Jira create results for raised tickets
- `.ics` generation for detected future meetings

## Run API Server (Backend Only)

```bash
poetry run uvicorn src.api.server:app --host 127.0.0.1 --port 8002
```

Health route:

- `GET /` -> JSON status payload

## Google Drive Cron Automation (New Files Only)

The Drive worker:

- polls the configured Drive folder
- supports text, Google Docs, and audio files (`audio/*`, `.wav`, `.mp3`, etc.)
- processes only unprocessed files (tracked by file ID)
- writes state and run reports under `src/output/`

Run once:

```bash
set -a
source .env
set +a
poetry run python src/services/drive_cron_worker.py
```

Output files:

- `src/output/drive_processed_files.json`
- `src/output/drive_runs/run_*.json`

### Cron Setup Example (every minute)

```cron
* * * * * cd /Users/bharathkumar/Multi-Agent-Worflow-to-Automate-Meet-Recordings && /usr/bin/env zsh -lc 'set -a; source .env; set +a; poetry run python src/services/drive_cron_worker.py >> src/output/drive_cron.log 2>&1'
```

## Google Drive Service Account Setup

1. Create/select a Google Cloud project.
2. Enable Google Drive API.
3. Create service account and JSON key.
4. Share target Drive folder with the service account email.
5. Set `GOOGLE_SERVICE_ACCOUNT_JSON` to the key file path.

## Important Behaviors

- Draft tickets are created when key fields are unclear/missing.
- Ready tickets are raised immediately in automated paths.
- Meeting summary generation runs in parallel with task extraction.
- Meeting invite generation avoids scheduling stale/past dates.

## Common Troubleshooting

- `**address already in use**`: change API port (`--port 8003`).
- `**command not found` while sourcing `.env**`: remove spaces around `=` in env file.
- **Drive worker processes 0 files**:
  - ensure files are in the configured folder
  - ensure MIME/file type is supported
  - ensure folder shared with service account
- **Google auth transport errors**:
  - check DNS/proxy/VPN
  - unset proxy vars and retry





