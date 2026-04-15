# LangGraph MVP - Meeting Recording to Jira Task Schema

This MVP covers only:
- raw call recording transcript text input
- extraction via a LangGraph extractor agent
- normalized output schema containing only `type="task"` events

It does **not** create Jira tickets yet (next step).

## Setup

```bash
poetry install
export GOOGLE_API_KEY="your_key_here"
```

## Run

```bash
poetry run python mvp_jira_extractor.py
```

The script includes a sample transcript and prints extracted task objects in JSON.

## Output Schema (task-only)

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
