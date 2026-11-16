"""Compliance guard agent (set B, W4).

run(ctx, task) -> dict per the agent interface. The agent behind the
shared compliance-guard registry entry.
task carries {"drafts_file": path} or {"text": str, "business": slug,
"recipient": email, "channel": str}.

Each item is checked through the real guard (core.guard.check_outbound)
when importable; if core/guard.py is not available, the same rules are
implemented inline and the fallback is noted in the log. Extra check
the guard module leaves to callers: the sender must be the business's
own inbox (from agents._common.BUSINESSES / businesses.yaml).

Writes runs/<date>/compliance-<slug>.json with
{passed, failed: [{item, reasons}], total, all_passed}.
Dry-run only: checks drafts, never sends anything.
"""
import json
import os

from agents._bcommon import (
    has_ai_label, load_core_guard, make_result, run_dir, slug_of,
    state_log, write_json,
)

try:
    from agents._common import BUSINESSES as _KNOWN_BUSINESSES
except Exception:
    _KNOWN_BUSINESSES = {}

_FALLBACK_USED = "core.guard not importable; inline rules used"


def _expected_inbox(slug):
    info = _KNOWN_BUSINESSES.get(slug or "") or {}
    return (info.get("email") or "").strip().lower()


def _inline_check(business_slug, recipient, subject, body, channel, source_tag=None):
    """Inline copy of the core.guard rules, used only if core.guard
    cannot be imported."""
    reasons = []
    recip = (recipient or "").lower()
    combined = ((subject or "") + "\n" + (body or "")).lower()
    if recip.startswith("info@"):
        reasons.append("recipient is an info@ address (info@ sends are banned)")
    if "integrityroofinginc.ca" in recip or "integrity roofing" in combined \
            or "integrityroofinginc.ca" in combined:
        reasons.append("Integrity Roofing must never be contacted")
    bad = has_ai_label(body) + has_ai_label(subject)
    for phrase in sorted(set(bad)):
        reasons.append("copy contains AI-label phrase: %r" % phrase)
    if business_slug == "bnb-media" and (channel or "email") == "email":
        if source_tag not in ("airbnb", "listing"):
            reasons.append("bnb-media outreach requires source_tag 'airbnb' or 'listing'")
    return (len(reasons) == 0, reasons)


def _check_item(item, check_outbound, check_money_action, require_sender=True):
    """Returns (passed: bool, reasons: list). require_sender is False for
    single-text checks, which carry no sender to verify."""
    business = item.get("business") or item.get("slug") or ""
    slug = str(business).strip().lower()
    recipient = item.get("to") or item.get("recipient") or ""
    subject = item.get("subject") or ""
    body = item.get("body") or item.get("text") or ""
    channel = (item.get("channel") or "email").strip().lower()
    source_tag = item.get("source_tag")
    reasons = []

    if check_outbound is not None:
        business_arg = {"slug": slug} if slug else None
        if business_arg is not None:
            info = _KNOWN_BUSINESSES.get(slug) or {}
            if info.get("phone"):
                business_arg["phone"] = info["phone"]
        ok, guard_reasons = check_outbound(business_arg or slug, recipient,
                                           subject, body, channel, source_tag)
        hard = [r for r in (guard_reasons or []) if not str(r).startswith("warning:")]
        reasons.extend(hard)
    else:
        ok, inline_reasons = _inline_check(slug, recipient, subject, body,
                                           channel, source_tag)
        reasons.extend(inline_reasons)

    # Own-inbox rule (caller's responsibility per core.guard docstring).
    # A wrong sender always fails; a missing sender fails only when a
    # sender is expected (drafts file), not for single-text checks.
    if channel == "email":
        sender = (item.get("from") or item.get("sender") or "").strip().lower()
        expected = _expected_inbox(slug)
        if expected:
            if not sender:
                if require_sender:
                    reasons.append("sender missing; own-inbox rule cannot be verified")
            elif sender != expected:
                reasons.append("sender %s is not the business inbox %s"
                               % (sender, expected))
        elif not sender and require_sender:
            reasons.append("sender missing; own-inbox rule cannot be verified")

    # Money / booking actions are always held for Jay's tap.
    if check_money_action is not None:
        if check_money_action(body) or check_money_action(subject):
            reasons.append("money/booking action held for Jay's tap")
    return (len(reasons) == 0, reasons)


def run(ctx, task):
    task = dict(task or {})
    slug = slug_of(ctx)
    log = []
    directory = run_dir(ctx, task)
    check_outbound, check_money_action = load_core_guard()
    if check_outbound is None:
        log.append(_FALLBACK_USED)
    else:
        log.append("using core.guard.check_outbound")

    items = []
    if task.get("drafts_file"):
        path = task["drafts_file"]
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            return make_result("compliance_guard", "error", [],
                               "Could not load drafts file %s: %s" % (path, exc),
                               ["drafts file unreadable: %s" % exc], log)
        if isinstance(data, dict):
            data = data.get("drafts") or data.get("items") or []
        if not isinstance(data, list):
            return make_result("compliance_guard", "error", [],
                               "Drafts file %s is not a JSON list." % path,
                               ["drafts file is not a JSON list"], log)
        items = data
        log.append("loaded %d drafts from %s" % (len(items), path))
    elif task.get("text") is not None:
        items = [{
            "text": task.get("text") or "",
            "business": task.get("business") or slug,
            "recipient": task.get("recipient") or "",
            "channel": task.get("channel") or "email",
            "source_tag": task.get("source_tag"),
        }]
    else:
        return make_result("compliance_guard", "held", [],
                           "Nothing to check: give drafts_file or text.",
                           ["no drafts_file or text in task"], log)

    passed = 0
    failed = []
    single_text = not task.get("drafts_file")
    for item in items:
        if not isinstance(item, dict):
            failed.append({"item": item, "reasons": ["draft is not an object"]})
            continue
        ok, reasons = _check_item(item, check_outbound, check_money_action,
                                  require_sender=not single_text)
        if ok:
            passed += 1
        else:
            failed.append({"item": item, "reasons": reasons})

    report = {
        "passed": passed,
        "failed": failed,
        "total": len(items),
        "all_passed": len(failed) == 0,
    }
    path = write_json(os.path.join(directory, "compliance-%s.json" % slug), report)
    state_log(ctx, "compliance_guard: %d/%d passed for %s"
              % (passed, len(items), slug))
    log.append("wrote %s" % path)
    summary = "Compliance check for %s: %d passed, %d failed of %d." % (
        slug, passed, len(failed), len(items))
    status = "ok" if not failed else "held"
    held = [] if not failed else ["%d draft(s) failed compliance" % len(failed)]
    return make_result("compliance_guard", status, [path], summary, held, log)
