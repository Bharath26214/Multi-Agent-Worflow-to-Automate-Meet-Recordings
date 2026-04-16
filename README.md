# LangGraph MVP - Meeting Recording to Jira Task Schema

This MVP covers only:
- raw call recording transcript text input
- extraction via a LangGraph extractor agent
- mapping extracted tasks into Jira "create issue" payload drafts

It does **not** call the Jira API yet (it only generates payload drafts).

## Setup

```bash
poetry install
export OPENAI_API_KEY="your_key_here"
# Optional: choose an OpenAI chat model
export OPENAI_MODEL="gpt-4o-mini"

# Optional: map extracted assignee names/emails to Jira accountIds
# Format: {"X":"abcd123", "x@company.com":"abcd123"}
export JIRA_ASSIGNEE_ACCOUNTID_MAP_JSON='{"X":"PUT_ACCOUNT_ID_HERE"}'
```

## Run

```bash
poetry run python mvp_jira_extractor.py

# Production-style src/ entrypoint:
poetry run python src/main.py
```

The script includes a sample transcript and prints:
- `extracted_tasks`: extracted `type="task"` events
- `jira_tickets_batch`: all Jira issues grouped in one Pydantic batch object

## Output Schema (tasks + Jira payloads)

Each extracted item follows:

- `event_id`
- `type` (always `"task"`)
- `spoken_by`
- `spoken_to`
- `description`
- `assigned_by`
- `assigned_to`
- `due_date`
- `priority`
- `t0`, `t1`
- `created_by` (always `"meet-agent"`)
- `confidence`
