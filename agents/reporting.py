"""Reporting agent (set B, W4).

run(ctx, task) -> dict per the agent interface.
Reads state + runs/runs.jsonl (if present) for the date and produces
runs/<date>/report-<business-or-all>.md with plain counts only, never
invented numbers:
  leads found, drafts written, follow-ups due, quotes built,
  posts drafted, held items with reasons, errors.
Shared agent (business "shared"): task {"scope": "all"} reports across
all businesses.
"""
import json
import os

from agents._bcommon import (
    make_result, run_date, run_dir, slug_of, state_get, state_log,
    write_text,
)

ALL_BUSINESSES = (
    "gutter-genie", "winnipeg-web-works", "move-and-metal", "bnb-media",
    "demoboss", "quonset-place", "drip-detailing",
)

_LEAD_TYPES = {"lead", "lead_found", "lead-found"}
_DRAFT_TYPES = {"draft", "email_draft", "email-draft", "outreach_draft"}
_FOLLOWUP_TYPES = {"followup", "follow-up", "followup_due", "follow_up_due"}
_QUOTE_TYPES = {"quote", "quote_built"}
_POST_TYPES = {"post", "post_draft", "post-draft"}


def _entry_kind(entry):
    """Classify one runs.jsonl entry into a count bucket (or None)."""
    kind = str(entry.get("type") or entry.get("kind") or "").strip().lower()
    agent = str(entry.get("agent") or "").strip().lower()
    event = str(entry.get("event") or "").strip().lower()
    if kind in _LEAD_TYPES or "lead" in agent or "lead" in event:
        return "leads"
    if kind in _QUOTE_TYPES or "quote_builder" in agent or "quote-builder" in agent or "quote" in event:
        return "quotes"
    if kind in _POST_TYPES or "social_creator" in agent or "social-creator" in agent or "post" in event:
        return "posts"
    if kind in _FOLLOWUP_TYPES or "follow" in agent or "follow" in event:
        return "followups"
    if kind in _DRAFT_TYPES or "outreach" in agent or "draft" in event:
        return "drafts"
    return None


def _entry_date(entry, fallback):
    date = entry.get("date")
    if date:
        return str(date)
    ts = str(entry.get("ts") or entry.get("timestamp") or "")
    if len(ts) >= 10:
        return ts[:10]
    return fallback


def _entry_business(entry):
    business = entry.get("business")
    if isinstance(business, dict):
        return str(business.get("slug") or "")
    return str(business or "")


def _runs_file_candidates(ctx, task):
    out_dir = ctx.get("out_dir") or ""
    candidates = []
    if task.get("runs_file"):
        candidates.append(task["runs_file"])
    candidates.append(os.path.join(out_dir, "runs.jsonl"))
    # The real log lives at repo root runs/runs.jsonl; out_dir is
    # runs/<date>, so its parent dir holds runs.jsonl.
    candidates.append(os.path.join(os.path.dirname(out_dir.rstrip("/")),
                                   "runs.jsonl"))
    seen = set()
    unique = []
    for path in candidates:
        key = os.path.abspath(path) if path else ""
        if key and key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _load_entries(ctx, task):
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
                        if isinstance(row, dict):
                            entries.append(row)
            except OSError:
                continue
    return entries


def _state_counts(ctx, date):
    """Best-effort counts from state keys, if the engine stored any."""
    counts = {"leads": 0, "drafts": 0, "followups": 0, "quotes": 0, "posts": 0}
    prefixes = {
        "leads": ("leads_found", "leads"),
        "drafts": ("drafts_written", "drafts"),
        "followups": ("followups_due", "followups"),
        "quotes": ("quotes_built", "quotes"),
        "posts": ("posts_drafted", "posts"),
    }
    for bucket, keys in prefixes.items():
        for key in keys:
            for suffix in ("", "_%s" % date):
                value = state_get(ctx, key + suffix)
                if isinstance(value, (int, float)):
                    counts[bucket] += int(value)
    return counts


def run(ctx, task):
    task = dict(task or {})
    slug = slug_of(ctx)
    scope = (task.get("scope") or "").strip().lower()
    all_scope = scope == "all" or slug == "shared"
    date = run_date(task)
    directory = run_dir(ctx, task)
    log = []

    entries = _load_entries(ctx, task)
    counts = {"leads": 0, "drafts": 0, "followups": 0, "quotes": 0, "posts": 0}
    held_items = []
    errors = []
    for entry in entries:
        if _entry_date(entry, date) != date:
            continue
        entry_slug = _entry_business(entry)
        if not all_scope and entry_slug and entry_slug != slug:
            continue
        kind = _entry_kind(entry)
        if kind:
            counts[kind] += 1
        status = str(entry.get("status") or "").strip().lower()
        if status == "held" or entry.get("held"):
            reasons = entry.get("held") or entry.get("reasons") or []
            if isinstance(reasons, str):
                reasons = [reasons]
            held_items.append({
                "agent": entry.get("agent") or "?",
                "business": entry_slug or "?",
                "reasons": list(reasons),
            })
        if status == "error" or entry.get("error"):
            errors.append(str(entry.get("error") or entry.get("summary") or "error"))

    state_counts = _state_counts(ctx, date)
    for key in counts:
        counts[key] += state_counts[key]

    name = "all businesses" if all_scope else slug
    filename = "report-all.md" if all_scope else "report-%s.md" % slug
    lines = ["DAILY REPORT - %s" % date.upper(), ""]
    lines.append("Scope: %s" % name)
    lines.append("")
    lines.append("COUNTS")
    lines.append("Leads found: %d" % counts["leads"])
    lines.append("Drafts written: %d" % counts["drafts"])
    lines.append("Follow-ups due: %d" % counts["followups"])
    lines.append("Quotes built: %d" % counts["quotes"])
    lines.append("Posts drafted: %d" % counts["posts"])
    lines.append("")
    lines.append("HELD ITEMS (%d)" % len(held_items))
    if held_items:
        for item in held_items:
            reasons = "; ".join(item["reasons"]) if item["reasons"] else "no reason recorded"
            lines.append("- %s (%s): %s" % (item["agent"], item["business"], reasons))
    else:
        lines.append("None.")
    lines.append("")
    lines.append("ERRORS (%d)" % len(errors))
    if errors:
        for err in errors:
            lines.append("- %s" % err)
    else:
        lines.append("None.")
    lines.append("")
    lines.append("Source: runs.jsonl entries for %s plus state counters. "
                 "Counts are from recorded runs only." % date)
    path = write_text(os.path.join(directory, filename), "\n".join(lines) + "\n")
    state_log(ctx, "reporting: report written for %s (%s)" % (name, date))
    log.append("wrote %s" % path)
    summary = ("Report for %s on %s: %d leads, %d drafts, %d follow-ups, "
               "%d quotes, %d posts, %d held, %d errors."
               % (name, date, counts["leads"], counts["drafts"],
                  counts["followups"], counts["quotes"], counts["posts"],
                  len(held_items), len(errors)))
    return make_result("reporting", "ok", [path], summary, [], log)
