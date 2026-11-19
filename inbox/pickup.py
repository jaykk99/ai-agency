"""Pickup loop for the AI agency dashboard queue (Agents space).

Watches ``~/workspace/ai-agency/inbox/`` for queue-contract task files
(``<task-id>.json`` with status "queued" or "sent"), runs each one
through ``supervisor.py`` in DRY-RUN mode only, and renames the file to
``<task-id>.done.json`` (or ``<task-id>.failed.json``).

Usage:
    python3 pickup.py --once            # one sweep, then exit (for cron)
    python3 pickup.py --loop 30         # poll forever, every 30s

Design notes:
- DRY-RUN IS HARD-CODED. The v1 agents have no send/publish
  implementation, and this loop never enables live mode.
- Claiming is atomic: a task file is renamed to ``<id>.running.json``
  before it runs, so two sweeps can never run the same task twice.
  A ``.running.json`` older than 30 minutes is treated as stale
  (crashed mid-run) and re-claimed.
- ``agent: "auto"`` is resolved to a concrete agent id from the
  instruction keywords + business prefix; unknown instructions fall
  back to the business's lead researcher, then ops-manager.
- The supervisor already appends one line per run to runs/runs.jsonl;
  this loop only moves the queue file and keeps its own log at
  runs/pickup.log.
- Scheduling: a cron runs ``pickup.py --once`` every 5 minutes, so a
  VM reboot only delays pickup until the next cron tick. No daemon
  to restart.
"""

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INBOX = ROOT / "inbox"
RUNS_LOG = ROOT / "runs" / "pickup.log"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from supervisor import Supervisor  # noqa: E402

STALE_RUNNING_SECONDS = 30 * 60

# instruction keyword -> role suffix (business prefix added at resolve time)
_ROLE_KEYWORDS = [
    (("lead", "prospect", "find", "hunt", "research"), "lead-researcher"),
    (("outreach", "email", "send", "first contact", "pitch"), "outreach-sender"),
    (("follow", "chase", "revive", "unanswered"), "followup-chaser"),
    (("quote", "price", "estimate"), "quote-builder"),
    (("audit",), "audit-builder"),
    (("schedule", "book", "visit", "meeting", "call"), "scheduler"),
    (("social", "post", "reel", "story", "facebook", "instagram"), "social-creator"),
    (("video",), "video-producer"),
    (("report", "summary", "daily report"), "reporting"),
    (("review",), "review-requester"),
    (("support", "customer", "question", "answer"), "customer-support"),
]

_BUSINESS_PREFIX = {
    "gutter-genie": "gg",
    "winnipeg-web-works": "www",
    "move-and-metal": "mm",
    "bnb-media": "bnb",
    "demoboss": "db",
    "quonset-place": "qp",
    "drip-detailing": "dd",
}


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _log(msg):
    line = "%s pickup: %s" % (_now_iso(), msg)
    print(line, flush=True)
    try:
        RUNS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(RUNS_LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def resolve_auto_agent(supervisor, business, instruction):
    """Pick a concrete agent id for agent:"auto"."""
    text = (instruction or "").lower()
    prefix = _BUSINESS_PREFIX.get(business or "")
    role = None
    for keywords, candidate in _ROLE_KEYWORDS:
        if any(k in text for k in keywords):
            role = candidate
            break
    candidates = []
    if prefix and role:
        candidates.append("%s-%s" % (prefix, role))
    if role:
        # Shared agents (reporting, ops-manager) have no business prefix.
        candidates.append(role)
    if prefix:
        candidates.append("%s-lead-researcher" % prefix)
    candidates.append("ops-manager")
    for agent_id in candidates:
        if supervisor.find_agent(agent_id) is not None:
            return agent_id
    return "ops-manager"


def _is_queue_file(path):
    """True for plain <task-id>.json files (not .done/.failed/.running)."""
    if path.suffix != ".json":
        return False
    parts = path.name.split(".")
    return len(parts) == 2  # <stem>.json only


def _claim(path):
    """Atomically claim a queue file by renaming to .running.json."""
    running = path.with_name(path.stem + ".running.json")
    try:
        path.rename(running)
    except OSError:
        return None
    return running


def _finish(running_path, task, status, result):
    """Write the outcome into the task file and rename to done/failed."""
    task = dict(task)
    task["status"] = status
    task["completed_at"] = _now_iso()
    if result is not None:
        task["result"] = {
            "status": result.get("status"),
            "summary": result.get("summary"),
            "outputs": result.get("outputs"),
            "held": result.get("held"),
        }
    suffix = ".done.json" if status == "done" else ".failed.json"
    target = running_path.with_name(running_path.name.replace(".running.json", suffix))
    try:
        running_path.write_text(json.dumps(task, indent=2), encoding="utf-8")
        running_path.rename(target)
    except OSError as exc:
        _log("could not finish %s: %s" % (running_path.name, exc))
        return None
    return target


def process_one(supervisor, path):
    """Pick up and run one queue file. Returns (task_id, outcome)."""
    task_id = path.stem
    running = _claim(path)
    if running is None:
        return task_id, "skip"
    try:
        task = json.loads(running.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _log("%s: unreadable task file (%s)" % (task_id, exc))
        _finish(running, {"id": task_id, "status": "failed",
                          "error": "unreadable task file: %s" % exc},
                "failed", None)
        return task_id, "failed"

    if not isinstance(task, dict):
        _log("%s: task file is not a JSON object" % task_id)
        _finish(running, {"id": task_id}, "failed", None)
        return task_id, "failed"

    status = str(task.get("status") or "queued").lower()
    if status not in ("queued", "sent"):
        # Already handled / unknown state: leave it alone, un-claim.
        _log("%s: status %r, not picking up" % (task_id, status))
        running.rename(path)
        return task_id, "skip"

    agent_id = task.get("agent")
    business = task.get("business")
    inner = task.get("task") or {}
    if not isinstance(inner, dict):
        inner = {"instruction": str(inner)}
    instruction = inner.get("instruction") or ""

    if agent_id == "auto" or not agent_id:
        agent_id = resolve_auto_agent(supervisor, business, instruction)
        task["resolved_agent"] = agent_id
        _log("%s: auto -> %s" % (task_id, agent_id))

    _log("%s: running agent=%s business=%s" % (task_id, agent_id, business))
    try:
        result = supervisor.run(
            {"agent": agent_id, "business": business, "task": inner},
            dry_run=True)
    except Exception as exc:  # supervisor.run should not raise; be safe
        _log("%s: supervisor raised: %s" % (task_id, exc))
        _finish(running, task, "failed",
                {"status": "error", "summary": "supervisor raised: %s" % exc,
                 "outputs": [], "held": []})
        return task_id, "failed"

    run_status = result.get("status")
    if run_status in ("ok", "held", "planned"):
        _finish(running, task, "done", result)
        _log("%s: %s (%s)" % (task_id, run_status, result.get("summary") or ""))
        return task_id, "done"
    _finish(running, task, "failed", result)
    _log("%s: FAILED (%s)" % (task_id, result.get("summary") or ""))
    return task_id, "failed"


def reclaim_stale():
    """Re-queue .running.json files older than the stale threshold."""
    now = time.time()
    reclaimed = 0
    for path in sorted(INBOX.glob("*.running.json")):
        try:
            age = now - path.stat().st_mtime
        except OSError:
            continue
        if age > STALE_RUNNING_SECONDS:
            target = path.with_name(path.name.replace(".running.json", ".json"))
            try:
                path.rename(target)
                _log("reclaimed stale %s (age %.0fs)" % (path.name, age))
                reclaimed += 1
            except OSError as exc:
                _log("could not reclaim %s: %s" % (path.name, exc))
    return reclaimed


def sweep():
    """One pass over the inbox. Returns (done, failed, skipped)."""
    supervisor = Supervisor(root=ROOT)
    reclaim_stale()
    done = failed = skipped = 0
    for path in sorted(INBOX.glob("*.json")):
        if not _is_queue_file(path):
            continue
        _, outcome = process_one(supervisor, path)
        if outcome == "done":
            done += 1
        elif outcome == "failed":
            failed += 1
        else:
            skipped += 1
    return done, failed, skipped


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    interval = None
    once = False
    i = 0
    while i < len(argv):
        if argv[i] == "--once":
            once = True
        elif argv[i] == "--loop" and i + 1 < len(argv):
            interval = float(argv[i + 1])
            i += 1
        else:
            print("usage: pickup.py [--once | --loop SECONDS]")
            return 2
        i += 1
    if interval is None and not once:
        once = True
    _log("sweep start (dry-run only)")
    if interval is not None:
        while True:
            done, failed, skipped = sweep()
            _log("sweep: %d done, %d failed, %d skipped" % (done, failed, skipped))
            time.sleep(interval)
    done, failed, skipped = sweep()
    _log("sweep: %d done, %d failed, %d skipped" % (done, failed, skipped))
    return 0


if __name__ == "__main__":
    sys.exit(main())
