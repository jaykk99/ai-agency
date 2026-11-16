"""Scheduler agent (set B, W4).

run(ctx, task) -> dict per the agent interface.
task carries {"contact": name, "requested": "2026-11-17 12:30", ...}.

Rules:
- On-site visits are weekdays at 12:30 PM only (Gutter Genie rule; applied
  as the default visit rule for all businesses in v1).
- A valid requested slot is proposed as-is. A slot that violates the rule
  gets the next valid weekday 12:30 slot proposed instead, status "held",
  reason "needs Jay confirm".
- Nothing is ever auto-booked. No calendar event is created in v1 (no
  calendar connection yet); this agent proposes only.
- Writes runs/<date>/schedule-<slug>.json with
  {contact, requested, proposed, status: proposed|held, reason}.
"""
import datetime
import os

from agents._bcommon import (
    make_result, run_dir, slug_of, state_log, state_set, write_json,
)

VISIT_HOUR = 12
VISIT_MINUTE = 30

_ACCEPTED_FORMATS = (
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
)


def parse_requested(raw):
    """Parse a requested time string; returns datetime or None."""
    if raw is None:
        return None
    if isinstance(raw, datetime.datetime):
        return raw
    text = str(raw).strip()
    for fmt in _ACCEPTED_FORMATS:
        try:
            return datetime.datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def is_valid_slot(when):
    """Weekday (Mon-Fri) at exactly 12:30."""
    return when.weekday() < 5 and when.hour == VISIT_HOUR and when.minute == VISIT_MINUTE


def next_valid_slot(when):
    """Next weekday 12:30 at or after `when`.

    If `when` is before 12:30 on a weekday, that same day 12:30 is valid.
    Otherwise advance day by day to the next weekday 12:30.
    """
    candidate = when.replace(hour=VISIT_HOUR, minute=VISIT_MINUTE, second=0, microsecond=0)
    if candidate < when:
        candidate += datetime.timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += datetime.timedelta(days=1)
    return candidate


def run(ctx, task):
    task = dict(task or {})
    slug = slug_of(ctx)
    log = []
    directory = run_dir(ctx, task)

    contact = (task.get("contact") or "").strip()
    if not contact:
        held = ["contact name is missing; cannot propose a visit"]
        log.append("held: missing contact")
        return make_result("scheduler", "held", [], "Schedule held: missing contact.", held, log)
    requested_raw = task.get("requested")
    requested = parse_requested(requested_raw)
    if requested is None:
        held = ["requested time %r could not be parsed (use YYYY-MM-DD HH:MM)" % (requested_raw,)]
        log.append("held: unparsable requested time")
        return make_result("scheduler", "held", [], "Schedule held: bad requested time.", held, log)

    fmt = "%Y-%m-%d %H:%M"
    if is_valid_slot(requested):
        proposed = requested
        status = "proposed"
        reason = ("Requested slot is a valid weekday 12:30 visit. Proposed only, "
                  "needs Jay confirm to book. No calendar event created.")
    else:
        proposed = next_valid_slot(requested)
        status = "held"
        if requested.weekday() >= 5:
            why = "requested day is a weekend"
        elif requested.hour != VISIT_HOUR or requested.minute != VISIT_MINUTE:
            why = "requested time is not 12:30 PM"
        else:
            why = "requested slot is not a valid visit slot"
        reason = ("On-site visits are weekdays at 12:30 PM only (%s). "
                  "Next valid slot proposed instead; needs Jay confirm to book. "
                  "No calendar event created." % why)

    record = {
        "contact": contact,
        "requested": requested.strftime(fmt),
        "proposed": proposed.strftime(fmt),
        "status": status,
        "reason": reason,
    }
    path = write_json(os.path.join(directory, "schedule-%s.json" % slug), record)
    state_set(ctx, "last_schedule_%s" % slug, path)
    state_log(ctx, "scheduler: %s -> proposed %s (%s)" % (contact, record["proposed"], status))
    log.append("wrote %s" % path)
    result_status = "ok" if status == "proposed" else "held"
    held = [] if status == "proposed" else ["needs Jay confirm"]
    summary = ("Visit for %s proposed at %s (%s)." % (contact, record["proposed"], status))
    return make_result("scheduler", result_status, [path], summary, held, log)
