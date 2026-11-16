"""Outreach sender agent (W3, set A) — DRY-RUN ONLY.

run(ctx, task) -> dict per the agent interface.
Loads leads from state (or runs/<date>/leads-<slug>.json), builds one
outreach email draft per lead in the business voice, passes every draft
through ctx["guard"], and writes drafts to
outbox/drafts-<slug>-<date>.json with status draft|held.

DRY-RUN: status is always draft/held, never sent. Outlook businesses
(Move & Metal, DemoBoss, Quonset Place) and Drip Detailing (no inbox):
ALL drafts held.
"""

import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents._common import (  # noqa: E402
    business_info, check_guard, is_blocked_recipient,
    state_get, state_log, ensure_dir,
    OUTLOOK_HELD_REASON, NO_INBOX_HELD_REASON,
)

PHONE = "204-972-0325"


def _first_name(name):
    return (name or "").split(",")[0].split()[0] if (name or "").split() else "there"


def build_draft(business, lead):
    """Subject + body in the business voice. No pricing in GG first outreach."""
    slug = business["slug"]
    name = lead.get("name") or "there"
    first = _first_name(name)
    sender = business.get("email") or ""
    sign = "Jay\n%s" % business.get("name", "")
    if slug != "bnb-media" and business.get("phone"):
        sign += "\nCall or text %s" % business["phone"]

    if slug == "gutter-genie":
        subject = "Quick free look at your gutters before the snow"
        body = ("Hi %s,\n\nI run Gutter Genie here in Winnipeg. "
                "I wanted to offer a free look at the gutters and downspouts "
                "at your place.\n\nThe first freeze is in and blocked gutters "
                "drip onto entrances, then turn to ice. A dry entrance matters "
                "with people coming and going all day.\n\nI can stop by for a "
                "free look on a weekday at 12:30 PM. I also do roof snow "
                "removal and Christmas light hanging if that helps this winter.\n\n"
                "Want me to book a time?\n\n%s" % (first, sign))
    elif slug == "winnipeg-web-works":
        subject = "Free digital credibility audit for %s" % name
        body = ("Hi %s,\n\nI run Winnipeg Web Works. I do a free digital "
                "credibility audit for local owners: your site, your reviews, "
                "and how you show up when someone searches for you.\n\n"
                "It takes me about an hour and I send you the findings, no "
                "obligation. If there is something worth fixing, I will tell "
                "you plainly what it would take.\n\nWant me to run yours?\n\n%s"
                % (first, sign))
    elif slug == "bnb-media":
        subject = "A video tour for %s, so guests see it before they book" % name
        body = ("Hi %s,\n\nI run Bnb Media in Winnipeg. I make walkthrough "
                "videos for short stay hosts, built from your own photos, "
                "$199 or $299.\n\nYour guests book from a phone and never "
                "visit first. A video tour shows the place before anyone "
                "commits. That cuts the back and forth and books the right "
                "guests.\n\nWant me to send a sample made from one of your "
                "listings?\n\nJay\nBnb Media" % first)
    else:
        subject = "Quick intro from %s" % business.get("name", "us")
        body = ("Hi %s,\n\nI run %s here in Winnipeg and wanted to reach out. "
                "Happy to have a quick chat about how we can help.\n\n%s"
                % (first, business.get("name", ""), sign))
    return {"to": (lead.get("email") or "").strip().lower(),
            "from": sender, "subject": subject, "body": body}


def run(ctx, task=None):
    task = task or {}
    ctx = ctx or {}
    business = business_info(ctx.get("business") or {})
    slug = business["slug"]
    out_dir = ctx.get("out_dir") or os.path.join(
        os.path.expanduser("~/workspace/ai-agency"), "runs")
    today = date.today().isoformat()
    log, held = [], []

    # Load leads: state first, else the researcher output file.
    leads = state_get(ctx.get("state"), "leads", None)
    if not leads and task.get("leads_file"):
        try:
            with open(task["leads_file"], encoding="utf-8") as fh:
                leads = json.load(fh)
        except (OSError, ValueError) as exc:
            log.append("could not read leads file: %s" % exc)
            leads = []
    if not leads:
        candidate = os.path.join(out_dir, "leads-%s.json" % slug)
        try:
            with open(candidate, encoding="utf-8") as fh:
                leads = json.load(fh)
            log.append("loaded %d leads from %s" % (len(leads), candidate))
        except (OSError, ValueError):
            leads = []
    if task.get("leads"):
        leads = task["leads"]

    provider = business.get("provider", "gmail")
    inbox_held_reason = None
    if provider == "outlook":
        inbox_held_reason = OUTLOOK_HELD_REASON
    elif provider == "none" or not business.get("email"):
        inbox_held_reason = NO_INBOX_HELD_REASON

    drafts = []
    n_drafted = n_held = 0
    for lead in leads or []:
        draft = build_draft(business, lead)
        draft["status"] = "draft"
        draft["held_reasons"] = []
        reasons = []
        if inbox_held_reason:
            reasons.append(inbox_held_reason)
        blocked, reason = is_blocked_recipient(draft["to"], draft["body"])
        if blocked:
            reasons.append(reason)
        if not reasons:
            allowed, guard_reasons = check_guard(ctx, draft)
            if not allowed:
                reasons.extend(guard_reasons)
        if reasons:
            draft["status"] = "held"
            draft["held_reasons"] = reasons
            n_held += 1
            held.extend("%s: %s" % (draft["to"], r) for r in reasons)
        else:
            n_drafted += 1
        drafts.append(draft)

    outbox = ensure_dir(os.path.join(out_dir, "outbox"))
    out_path = os.path.join(outbox, "drafts-%s-%s.json" % (slug, today))
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(drafts, fh, indent=2)
    state_log(ctx.get("state"), "outreach_sender %s: %d drafted, %d held"
              % (slug, n_drafted, n_held))
    summary = ("outreach_sender %s: %d leads, %d drafted, %d held "
               "(dry-run, nothing sent)" % (slug, len(drafts), n_drafted, n_held))
    log.append(summary)
    status = "held" if inbox_held_reason and drafts else "ok"
    return {"status": status, "agent": "outreach_sender",
            "outputs": [out_path], "summary": summary,
            "held": held, "log": log}
