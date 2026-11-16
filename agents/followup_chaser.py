"""Follow-up chaser agent (W3, set A) — DRY-RUN ONLY, never sends.

run(ctx, task) -> dict per the agent interface.
Finds stale threads needing follow-up:
 (a) for Gmail businesses, search Gmail for sent outreach with no reply
     (best-effort via tools.gmail_read; if the CLI is unavailable or
     returns nothing, fall back to (b));
 (b) state follow-up queue + the SENT.md record at
     ~/workspace/daily-ops/leads/2026-11-16-fresh/SENT.md for threads
     sent Nov 15-16 with no recorded reply.
Writes runs/<date>/followups-<slug>.json. Outlook businesses: held.
"""

import json
import os
import re
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents._common import (  # noqa: E402
    business_info, state_get, state_log, ensure_dir,
    OUTLOOK_HELD_REASON, NO_INBOX_HELD_REASON,
)

try:
    from tools.gmail_read import search_messages, read_message
except Exception:  # pragma: no cover
    search_messages = None
    read_message = None

SENT_MD = os.path.expanduser(
    "~/workspace/daily-ops/leads/2026-11-16-fresh/SENT.md")
PHONE = "204-972-0325"

# "- email (Name) - campaign N" lines in SENT.md
_SENT_LINE_RE = re.compile(
    r"^-\s+([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})"
    r"(?:\s*\(([^)]*)\))?")
_SECTION_RE = re.compile(
    r"^##\s+(Gutter Genie|Winnipeg Web Works|Bnb Media|Move & Metal|"
    r"DemoBoss|Quonset Place|Drip Detailing)\b", re.I)
_SECTION_SLUG = {
    "gutter genie": "gutter-genie",
    "winnipeg web works": "winnipeg-web-works",
    "bnb media": "bnb-media",
    "move & metal": "move-and-metal",
    "demoboss": "demoboss",
    "quonset place": "quonset-place",
    "drip detailing": "drip-detailing",
}
# SENT.md is dated 2026-11-16; threads sent Nov 15-16.
_SENT_DATE = date(2026, 11, 16)


def _parse_sent_md(path, slug):
    """Threads sent for this business per SENT.md. Returns list of dicts."""
    items = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return items
    current = None
    for line in text.splitlines():
        m = _SECTION_RE.match(line)
        if m:
            current = _SECTION_SLUG.get(m.group(1).lower())
            continue
        if current != slug:
            continue
        m = _SENT_LINE_RE.match(line.strip())
        if m:
            items.append({"email": m.group(1).lower(),
                          "contact": (m.group(2) or "").strip(),
                          "last_contact": _SENT_DATE.isoformat(),
                          "source": "SENT.md"})
    return items


def _draft_reply(business, item, days):
    parts = (item.get("contact") or "").split(",")[0].split()
    first = parts[0] if parts else "there"
    slug = business["slug"]
    if slug == "bnb-media":
        body = ("Hi %s,\n\nJust checking in on my note about a video tour "
                "for your listing, built from your own photos, $199 or $299. "
                "Want me to send a sample?\n\nJay\nBnb Media" % first)
    elif slug == "winnipeg-web-works":
        body = ("Hi %s,\n\nJust following up on the free audit offer. Happy "
                "to run it whenever you like, no obligation.\n\nJay\n"
                "Winnipeg Web Works\nCall or text %s" % (first, PHONE))
    else:
        body = ("Hi %s,\n\nJust checking in on my offer of a free look at "
                "your gutters before the snow. I can stop by on a weekday at "
                "12:30 PM if that still helps.\n\nJay\n%s\nCall or text %s"
                % (first, business.get("name", ""), PHONE))
    if days >= 3:
        step = "Send follow-up now (no reply in %d days)." % days
    else:
        step = ("Wait until day 3, then send a short check-in "
                "(only %d day(s) since contact)." % days)
    return step, body


def run(ctx, task=None):
    task = task or {}
    ctx = ctx or {}
    business = business_info(ctx.get("business") or {})
    slug = business["slug"]
    out_dir = ctx.get("out_dir") or os.path.join(
        os.path.expanduser("~/workspace/ai-agency"), "runs")
    today = date.today()
    today_s = today.isoformat()
    log, held = [], []

    if business.get("provider") == "outlook":
        held.append(OUTLOOK_HELD_REASON)
    elif business.get("provider") == "none" or not business.get("email"):
        held.append(NO_INBOX_HELD_REASON)

    items = []
    # (a) Gmail search, best-effort.
    account_id = business.get("gmail_account_id") or task.get("gmail_account_id")
    gmail_used = False
    if (business.get("provider") == "gmail" and account_id and search_messages
            and not task.get("skip_gmail")):
        try:
            sent = search_messages(
                account_id, "in:sent after:2026/11/12", max_results=10)
            if sent:
                gmail_used = True
                sent_emails = set()
                # +triage rows carry no recipient; read each sent message
                # (capped at 5; each read is a slow CLI call) to get To.
                for msg in sent[:5]:
                    full = None
                    if msg.get("id"):
                        try:
                            full = read_message(account_id, msg["id"])
                        except Exception:
                            full = None
                    targets = (full or {}).get("to") or []
                    for em in targets:
                        sent_emails.add(str(em).lower())
                # Replies would arrive in inbox; check for any reply sender.
                inbox = search_messages(account_id, "in:inbox after:2026/11/12",
                                        max_results=10)
                replied = set()
                for msg in inbox or []:
                    frm = (msg.get("from") or "").lower()
                    for em in re.findall(
                            r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", frm):
                        replied.add(em)
                for em in sorted(sent_emails - replied):
                    items.append({"email": em, "contact": "",
                                  "last_contact": "2026-11-16",
                                  "source": "gmail-sent-no-reply"})
                log.append("gmail: %d sent, %d with no reply seen"
                           % (len(sent_emails), len(items)))
        except Exception as exc:
            log.append("gmail search failed, falling back to SENT.md: %s" % exc)
    if not gmail_used:
        log.append("gmail search unavailable/empty, using SENT.md + state queue")

    # (b) state follow-up queue
    queue = state_get(ctx.get("state"), "followup_queue", []) or []
    seen = {i.get("email") for i in items}
    for q in queue:
        if isinstance(q, dict) and q.get("email") and q["email"] not in seen:
            seen.add(q["email"])
            items.append({"email": q["email"].lower(),
                          "contact": q.get("contact", ""),
                          "last_contact": q.get("last_contact", "2026-11-16"),
                          "source": "state-queue"})

    # (b) SENT.md record
    sent_path = task.get("sent_md_path") or SENT_MD
    for entry in _parse_sent_md(sent_path, slug):
        if entry["email"] in seen:
            continue
        seen.add(entry["email"])
        items.append(entry)

    followups = []
    for item in items:
        try:
            last = date.fromisoformat(item.get("last_contact", "2026-11-16"))
        except ValueError:
            last = _SENT_DATE
        days = max(0, (today - last).days)
        step, draft = _draft_reply(business, item, days)
        followups.append({
            "thread": item["email"],
            "contact": item.get("contact") or item["email"],
            "last_contact": item.get("last_contact"),
            "days_waiting": days,
            "suggested_next_step": step,
            "draft_reply": draft,
            "source": item.get("source", ""),
            "status": "held" if held else "draft",
            "held_reasons": list(held),
        })

    runs_dir = ensure_dir(out_dir)
    out_path = os.path.join(runs_dir, "followups-%s.json" % slug)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(followups, fh, indent=2)
    state_log(ctx.get("state"), "followup_chaser %s: %d follow-ups"
              % (slug, len(followups)))
    summary = ("followup_chaser %s: %d threads need follow-up "
               "(dry-run, nothing sent)" % (slug, len(followups)))
    log.append(summary)
    return {"status": "held" if held else "ok", "agent": "followup_chaser",
            "outputs": [out_path], "summary": summary,
            "held": held, "log": log}
