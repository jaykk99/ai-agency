# Agents UI pickup loop

Wires the Agents dashboard (space `agents`) to the agency supervisor so
tasks Jay submits actually run. Built 2026-11-19.

## How it works

1. Jay submits a task in the Agents dashboard. The dashboard writes one
   queue-contract JSON file per task to `~/workspace/ai-agency/inbox/`
   (`<task-id>.json`, status `queued`, flipped to `sent` by
   markTaskDelivered).
2. Every 5 minutes the cron `agents-pickup-loop` runs one sweep:
   `python3 ~/workspace/ai-agency/inbox/pickup.py --once`
3. `inbox/pickup.py` (stdlib only) claims each queued/sent file by
   renaming it to `<task-id>.running.json` (atomic, so two sweeps can
   never run the same task), runs it through `supervisor.py`, and
   renames it to `<task-id>.done.json` (or `<task-id>.failed.json`)
   with the result embedded.
4. The supervisor appends one line per run to `runs/runs.jsonl`
   (dry_run: true). The dashboard's daily refresh (07:44) re-reads the
   snapshot, so completed runs show up in Latest Activity.

## Rules baked into the loop

- **DRY-RUN ONLY, hard-coded.** `pickup.py` always calls the
  supervisor with `dry_run=True`. The v1 agents have no send/publish
  implementation, and this loop must never enable live mode or build a
  send layer.
- `agent: "auto"` is resolved from the instruction keywords + business
  prefix (e.g. "report" -> shared `reporting` agent, "find hosts" ->
  `bnb-lead-researcher`). Unmatched instructions fall back to the
  business's lead researcher, then `ops-manager`.
- A `.running.json` older than 30 minutes is treated as stale (crashed
  mid-run) and re-queued on the next sweep.
- Unreadable / malformed task files are moved to `.failed.json` with
  the reason recorded; the loop never crashes the sweep on one bad file.

## Files

- `inbox/pickup.py` — the loop (sweep + auto-agent resolver).
- `inbox/QUEUE_CONTRACT.md` — the queue contract.
- `runs/pickup.log` — per-sweep log (start, per-task outcome, totals).
- `runs/runs.jsonl` — one line per agent run (the supervisor writes it).
- Cron: `agents-pickup-loop`, every 5 min, owner `space:agents`,
  timeout 1200s. Definition:
  `~/workspace/cron.d/minutely/agents-pickup-loop__interval@5m.md`

## Restart / recovery

- The cron is the persistence mechanism. VM reboots (~2h cadence) kill
  nothing durable: the next cron tick (within 5 min of boot) sweeps
  the inbox again. There is no daemon to restart.
- Check it: `cron.status --id agents-pickup-loop` (or `cron.list`),
  and `tail ~/workspace/ai-agency/runs/pickup.log`.
- Manual sweep: `python3 ~/workspace/ai-agency/inbox/pickup.py --once`
- Manual run now: `cron.run --id agents-pickup-loop`
- Disable: `cron.remove --id agents-pickup-loop` (or set enabled false
  via `cron.update`). Disabling means dashboard tasks pile up queued
  and never run.
- If a task is stuck in `.running.json` for over 30 min, the next
  sweep re-queues it automatically. If a task failed, read
  `inbox/<task-id>.failed.json` for the reason; do not re-run it by
  hand without checking why it failed.

## End-to-end test (2026-11-19)

- Submitted via the dashboard's own `submittask` action:
  `task-20261119-001`, agent `reporting`, business `gutter-genie`,
  "Build today's plain-counts report for gutter-genie. Pickup-loop
  end-to-end test from the Agents dashboard, dry-run only."
- Delivery agent wrote `inbox/task-20261119-001.json`; `marktaskdelivered`
  returned ok.
- Pickup sweep ran it in ~0.7s: status `ok`, dry_run true, summary
  "Report for gutter-genie on 2026-11-19: 1 leads, 0 drafts,
  0 follow-ups, 0 quotes, 0 posts, 0 held, 0 errors."
- `runs/runs.jsonl` grew 110 -> 111 lines; file renamed to
  `task-20261119-001.done.json`; dashboard `getdashboard` shows the
  task with status `done`.
- Two earlier smoke tests (manual queue files): `task-test-pickup-001`
  (auto -> gg-lead-researcher, ok) and `task-test-pickup-002`
  (auto -> reporting, ok). Both `.done.json` in the inbox.
- Note: the dashboard snapshot (`dashboard-data/agents.json`) still
  showed `generated: 2026-11-18` right after the test; the
  `refreshdashboard` action runs async and the 07:44 daily refresh
  regenerates it. The task's `done` state is already visible via
  `getdashboard` (tasks list).
