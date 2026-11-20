"""Ops manager agent (shared, operations).

run(ctx, task) -> dict per the agent interface.
Owns the daily run across all 7 businesses: queues work, watches
agent health, and keeps the dry-run/live boundary intact.

task carries:
  {"mode": "daily"} (default) and optionally
  {"registry_file": path, "runs_file": path}

Reads agents/registry.yaml (via the supervisor's YAML loader, so
both the wrapped and bare-list shapes work) and runs/runs.jsonl for
the run date, then writes runs/<date>/ops-plan-<date>.md: the daily
run plan (working agents in registry order), an agent health
summary from today's log, and anything blocked or stalled. A live
run is refused here the same way the runner refuses it: if the task
asks for live mode, the agent holds and does nothing live.
Dry-run only: plans and reports, never sends, books, or charges.
"""
import json
import os

from agents._bcommon import (
    make_result, run_date, run_dir, slug_of, state_log, write_text,
)

ALL_STATUSES = ("ok", "held", "error", "planned")


def _load_registry(registry_file):
    """Load the registry list via supervisor's loader (lazy import so
    this module never creates an import cycle)."""
    try:
        from supervisor import load_yaml, unwrap_list
    except Exception as exc:
        return None, "could not import supervisor loader: %s" % exc
    try:
        return unwrap_list(load_yaml(registry_file), "agents"), None
    except FileNotFoundError:
        return None, "registry file not found: %s" % registry_file
    except Exception as exc:
        return None, "could not load registry: %s" % exc


def _runs_file_candidates(ctx, task):
    out_dir = ctx.get("out_dir") or ""
    candidates = []
    if task.get("runs_file"):
        candidates.append(task["runs_file"])
    candidates.append(os.path.join(out_dir, "runs.jsonl"))
    candidates.append(os.path.join(
        os.path.dirname(out_dir.rstrip("/")), "runs.jsonl"))
    seen = set()
    unique = []
    for path in candidates:
        key = os.path.abspath(path) if path else ""
        if key and key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _load_today_entries(ctx, task, date):
    entries = []
    for path in _runs_file_candidates(ctx, task):
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            row = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(row, dict) \
                                and str(row.get("date") or "") == date:
                            entries.append(row)
            except OSError:
                continue
    return entries


def health_summary(registry, entries):
    """Per-agent health from today's log entries.

    Returns {agent_id: {"status": ..., "runs": n, "last": summary}}.
    Agents with no run today are "not run". Registry entries that are
    still plan-mode are flagged as "planned" (not working yet).
    """
    health = {}
    for entry in registry or []:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        agent_id = entry["id"]
        status = str(entry.get("status") or "defined").lower()
        health[agent_id] = {
            "registry_status": status,
            "module": entry.get("module"),
            "business": entry.get("business"),
            "runs": 0,
            "last_status": ("planned (defined only)"
                            if status == "defined" or not entry.get("module")
                            else "not run"),
            "last_summary": "",
        }
    for row in entries:
        agent_id = str(row.get("agent") or "")
        if agent_id not in health:
            health[agent_id] = {"registry_status": "unknown",
                                "module": None, "business": None,
                                "runs": 0, "last_status": "not run",
                                "last_summary": ""}
        health[agent_id]["runs"] += 1
        health[agent_id]["last_status"] = str(
            row.get("status") or "error")
        health[agent_id]["last_summary"] = str(row.get("summary") or "")
    return health


def run(ctx, task):
    task = dict(task or {})
    slug = slug_of(ctx)
    date = run_date(task)
    directory = run_dir(ctx, task)
    log = []

    if task.get("live") or str(task.get("mode") or "").lower() == "live":
        return make_result(
            "ops_manager", "held", [],
            "Live run refused: the ops manager keeps the dry-run/live "
            "boundary intact and never switches a run live on its own.",
            ["live mode refused; dry-run only"], log)

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    registry_file = task.get("registry_file") or os.path.join(
        root, "agents", "registry.yaml")
    registry, error = _load_registry(registry_file)
    if error:
        return make_result("ops_manager", "error", [], error, [error], log)

    entries = _load_today_entries(ctx, task, date)
    health = health_summary(registry, entries)
    working = [e for e in registry
               if isinstance(e, dict) and e.get("id")
               and str(e.get("status") or "").lower() == "working"
               and e.get("module")]
    blocked = [aid for aid, h in health.items()
               if h["last_status"] == "error"]
    planned = [aid for aid, h in health.items()
               if h["registry_status"] == "defined"]
    # "Stalled" only means something once the day is underway. When
    # this plan is the first run of the day (no log entries yet),
    # nothing is stalled: the plan starts the day.
    day_started = len(entries) > 0
    stalled = [aid for aid, h in health.items()
               if day_started and h["last_status"] == "not run"
               and h["registry_status"] == "working"]

    lines = ["OPS PLAN - %s" % date.upper(), ""]
    lines.append("Daily sequence (%d working agents):" % len(working))
    for entry in working:
        lines.append("- %s (%s)" % (entry["id"], entry.get("business")))
    lines.append("")
    lines.append("AGENT HEALTH TODAY")
    for agent_id in sorted(health):
        h = health[agent_id]
        lines.append("- %s: %s (runs: %d)%s"
                     % (agent_id, h["last_status"], h["runs"],
                        (" - %s" % h["last_summary"])
                        if h["last_summary"] else ""))
    lines.append("")
    lines.append("BLOCKED (%d): %s"
                 % (len(blocked),
                    ", ".join(blocked) if blocked else "none"))
    if not day_started:
        lines.append("No runs yet today; this plan starts the day.")
    lines.append("STALLED, NOT RUN (%d): %s"
                 % (len(stalled),
                    ", ".join(stalled) if stalled else "none"))
    lines.append("STILL PLAN-MODE (%d): %s"
                 % (len(planned),
                    ", ".join(planned) if planned else "none"))
    lines.append("")
    lines.append("Boundary: dry-run only. Live sends need Jay's approval "
                 "and approvals/live-approved.txt.")
    path = write_text(
        "%s/ops-plan-%s.md" % (directory, date), "\n".join(lines) + "\n")
    state_log(ctx, "ops_manager: plan for %s (%d working, %d blocked, "
                   "%d stalled)" % (date, len(working), len(blocked),
                                    len(stalled)))
    log.append("wrote %s" % path)
    held = []
    held += ["agent %s errored today" % aid for aid in blocked]
    held += ["agent %s stalled (not run today)" % aid for aid in stalled]
    summary = ("Ops plan for %s: %d working agents, %d blocked, %d "
               "stalled, %d still plan-mode."
               % (date, len(working), len(blocked), len(stalled),
                  len(planned)))
    status = "held" if held else "ok"
    return make_result("ops_manager", status, [path], summary, held, log)
