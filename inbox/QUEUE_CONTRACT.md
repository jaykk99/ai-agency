# Agent task queue contract

Tasks for the AI agency agents land here as one JSON file per task.

File: `~/workspace/ai-agency/inbox/<task-id>.json`

```json
{
  "id": "task-20261118-001",
  "agent": "gg-lead-researcher",
  "business": "gutter-genie",
  "task": {"instruction": "Find 10 new Gutter Genie prospects in Steinbach"},
  "source": "agent-ops-dashboard",
  "created_at": "2026-11-18T21:30:00-05:00",
  "status": "queued"
}
```

- `agent` must be one of the 50 ids in `agents/registry.yaml`.
- `business` must be one of the 7 business slugs.
- `agent: "auto"` means the supervisor picks the best agent for the business and task.
- The bridge (a scheduled job) picks up queued files, runs them through
  `supervisor.py`, writes the result to `runs/runs.jsonl`, and marks the
  file `done` (renamed to `<task-id>.done.json`) or `failed`.
- Live-send guard: the supervisor enforces the standing guard rules
  (own inbox only, no info@, no AI label, Bnb scope, Integrity Roofing block).
  Outreach senders are dry-run only until the send layer is built and approved.
