"""Quality control agent (shared, quality).

run(ctx, task) -> dict per the agent interface.
Reviews agent output before it reaches a customer: checks tone,
accuracy, pricing, and standing-rule compliance.

task carries either:
  {"drafts_file": path}  (JSON list of draft dicts, or {"drafts": [...]})
  {"drafts": [{to, from, subject, body, channel, business, source_tag}]}
  {"text": str, "business": slug, "channel": str, ...}  (single check)

Checks per draft:
  - no AI-label phrases ("built by AI", "made by AI", ...)
  - no em dash (Jay's voice is short and plain, dash-free)
  - phone 204-972-0325 present on email/facebook/post copy, except
    bnb-media (no phone in copy) and instagram (DM call-to-action)
  - recipient is not an info@ address and does not mention
    Integrity Roofing
  - Gutter Genie quotes: breakdown lines add up to ESTIMATED TOTAL
    and the 30% deposit matches, when those figures are present
  - the ctx guard, when one is wired, must also pass the draft

Writes runs/<date>/quality-<slug>.json with the pass/fail review and
a fix list per failed draft. Dry-run only: reviews drafts, never
sends anything.
"""
import json
import os
import re

from agents._bcommon import (
    has_ai_label, ctx_guard_check, make_result, run_date, run_dir,
    slug_of, state_log, write_json,
)

PHONE = "204-972-0325"
EM_DASH = "\u2014"
MONEY_RE = re.compile(r"\$([0-9][0-9,]*(?:\.[0-9]{2})?)")
TOTAL_RE = re.compile(r"ESTIMATED TOTAL:\s*\$([0-9][0-9,]*(?:\.[0-9]{2})?)")
DEPOSIT_RE = re.compile(r"30%\s*deposit\s*\(\$([0-9][0-9,]*(?:\.[0-9]{2})?)\)")
LINE_RE = re.compile(r"^[^:\n]+:\s*\$([0-9][0-9,]*(?:\.[0-9]{2})?)\s*$",
                     re.MULTILINE)


def _money(text):
    try:
        return round(float(str(text).replace(",", "")), 2)
    except (TypeError, ValueError):
        return None


def _check_quote_math(body, slug):
    """Verify a Gutter Genie quote's figures add up. Returns fix list."""
    fixes = []
    if slug != "gutter-genie":
        return fixes
    total_match = TOTAL_RE.search(body or "")
    if not total_match:
        return fixes
    total = _money(total_match.group(1))
    if total is None:
        return fixes
    breakdown = body.split("ESTIMATE BREAKDOWN", 1)[-1]
    breakdown = breakdown.split("ESTIMATED TOTAL", 1)[0]
    amounts = [_money(m) for m in LINE_RE.findall(breakdown)]
    amounts = [a for a in amounts if a is not None]
    if amounts and round(sum(amounts), 2) != total:
        fixes.append("breakdown lines sum to $%.2f but ESTIMATED TOTAL "
                     "says $%.2f" % (round(sum(amounts), 2), total))
    deposit_match = DEPOSIT_RE.search(body or "")
    if deposit_match:
        deposit = _money(deposit_match.group(1))
        if deposit is not None and round(total * 0.30, 2) != deposit:
            fixes.append("30%% deposit should be $%.2f, not $%.2f"
                         % (round(total * 0.30, 2), deposit))
    return fixes


def check_draft(draft, ctx):
    """Return a list of fix strings (empty = pass)."""
    fixes = []
    slug = str(draft.get("business") or draft.get("slug") or
               slug_of(ctx)).strip().lower()
    channel = str(draft.get("channel") or "email").strip().lower()
    subject = draft.get("subject") or ""
    body = draft.get("body") or draft.get("text") or ""
    recipient = (draft.get("to") or draft.get("recipient") or "").strip()

    bad = sorted(set(has_ai_label(body) + has_ai_label(subject)))
    for phrase in bad:
        fixes.append("remove AI-label phrase: %r" % phrase)
    if EM_DASH in body or EM_DASH in subject:
        fixes.append("remove em dash (Jay's voice is dash-free)")
    if recipient.lower().startswith("info@"):
        fixes.append("recipient is an info@ address (info@ sends are banned)")
    combined = (subject + "\n" + body).lower()
    if "integrity roofing" in combined or "integrityroofinginc.ca" in combined:
        fixes.append("copy mentions Integrity Roofing, never contact them")
    if channel in ("email", "facebook", "post") and slug != "bnb-media":
        if PHONE not in body:
            fixes.append("add phone %s to the copy" % PHONE)
    fixes.extend(_check_quote_math(body, slug))

    allowed, guard_reasons = ctx_guard_check(ctx, {
        "to": recipient,
        "from": draft.get("from") or draft.get("sender") or "",
        "subject": subject,
        "body": body,
        "channel": channel,
        "source_tag": draft.get("source_tag"),
    })
    if not allowed:
        for reason in guard_reasons:
            if not str(reason).startswith("warning:"):
                fixes.append("guard: %s" % reason)
    return fixes


def _load_drafts(task):
    """Return (drafts, error)."""
    if task.get("drafts_file"):
        path = task["drafts_file"]
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            return None, "could not load drafts file %s: %s" % (path, exc)
        if isinstance(data, dict):
            data = data.get("drafts") or data.get("items") or []
        if not isinstance(data, list):
            return None, "drafts file %s is not a JSON list" % path
        return data, None
    if task.get("drafts") is not None:
        drafts = task["drafts"]
        if not isinstance(drafts, list):
            return None, "drafts must be a list"
        return drafts, None
    if task.get("text") is not None:
        return [{
            "text": task.get("text") or "",
            "business": task.get("business") or "",
            "recipient": task.get("recipient") or "",
            "channel": task.get("channel") or "email",
            "source_tag": task.get("source_tag"),
        }], None
    return None, ("nothing to review: give drafts_file, drafts, "
                  "or text")


def run(ctx, task):
    task = dict(task or {})
    slug = slug_of(ctx)
    date = run_date(task)
    directory = run_dir(ctx, task)
    log = []

    drafts, error = _load_drafts(task)
    if error:
        return make_result("quality_control", "held", [], error,
                           [error], log)
    passed = 0
    failed = []
    for item in drafts:
        if not isinstance(item, dict):
            failed.append({"draft": item,
                           "fixes": ["draft is not an object"]})
            continue
        fixes = check_draft(item, ctx)
        if fixes:
            failed.append({"draft": item, "fixes": fixes})
        else:
            passed += 1

    report = {
        "business": slug,
        "date": date,
        "passed": passed,
        "failed": failed,
        "total": len(drafts),
        "all_passed": len(failed) == 0,
    }
    path = write_json(
        "%s/quality-%s.json" % (directory, slug), report)
    state_log(ctx, "quality_control: %s %d/%d drafts passed"
              % (slug, passed, len(drafts)))
    log.append("wrote %s" % path)
    summary = ("Quality review for %s: %d passed, %d failed of %d."
               % (slug, passed, len(failed), len(drafts)))
    status = "ok" if not failed else "held"
    held = [] if not failed else [
        "%d draft(s) failed quality review" % len(failed)]
    return make_result("quality_control", status, [path], summary,
                       held, log)
