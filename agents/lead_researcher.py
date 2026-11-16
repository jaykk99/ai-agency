"""Lead researcher agent (W3, set A).

run(ctx, task) -> dict per the agent interface.
task may carry {"niche": str, "region": str} (defaults: business services,
Winnipeg/Manitoba). Optional test hooks in task: "skip_web" (bool),
"used_emails" (set), "used_emails_path" (str), "local_leads_file" (str).

Steps:
 (a) web search for fresh prospects in niche/region;
 (b) read the existing local lead file for this business and extract
     name/email/source lines;
 (c) dedupe against used-emails.txt and state leads; drop info@ and other
     generic role addresses; never Integrity Roofing;
 (d) for Bnb, keep only sources tagged airbnb/listing;
 (e) write runs/<date>/leads-<slug>.json and save to state.
"""

import json
import os
import re
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents._common import (  # noqa: E402
    business_info, is_blocked_recipient, load_used_emails,
    state_get, state_set, state_log, ensure_dir,
)

try:
    from tools.websearch import search as web_search
except Exception:  # pragma: no cover
    web_search = None

LEADS_DIR = os.path.expanduser("~/workspace/daily-ops/leads/2026-11-16-fresh")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_HEADING_RE = re.compile(r"^##\s+\d+\.\s+(.+?)\s*$")
_EMAIL_LINE_RE = re.compile(r"^[-*]\s*Email:\s*(\S+)", re.I)
_SOURCE_LINE_RE = re.compile(r"^[-*]\s*Source:\s*(.+?)\s*$", re.I)
_CONTACT_LINE_RE = re.compile(r"^[-*]\s*Contact:\s*(.+?)\s*$", re.I)

# source text that marks a Bnb-eligible listing source
_LISTING_MARKERS = ("airbnb", "listing", "lodges", "travelmanitoba",
                    "outfitter", "bed and breakfast", "b&b", "vrbo",
                    "directory")


def _parse_local_leads(path):
    """Extract {name, email, source} entries from a per-business .md file."""
    leads = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return leads
    current = None
    for line in text.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            if current and current.get("email"):
                leads.append(current)
            current = {"name": m.group(1).strip(), "email": "",
                       "source": "", "contact": ""}
            continue
        if current is None:
            continue
        m = _EMAIL_LINE_RE.match(line.strip())
        if m:
            current["email"] = m.group(1).strip()
            continue
        m = _SOURCE_LINE_RE.match(line.strip())
        if m:
            current["source"] = m.group(1).strip()
            continue
        m = _CONTACT_LINE_RE.match(line.strip())
        if m:
            current["contact"] = m.group(1).strip()
    if current and current.get("email"):
        leads.append(current)
    return leads


def _tag_source(source, niche):
    s = (source or "").lower()
    if any(mk in s for mk in _LISTING_MARKERS):
        return "listing"
    if "airbnb" in (niche or "").lower():
        return "listing"
    return "web" if source else "local"


def _qualify_note(ctx, lead):
    bro = ctx.get("bro") if isinstance(ctx, dict) else None
    if not callable(bro):
        return "Fresh %s lead in Winnipeg/Manitoba; verify on own site before outreach." % lead.get("niche", "")
    try:
        note = bro("One-line qualification note for this lead, plain and short: "
                   "%s (%s), source %s, niche %s."
                   % (lead.get("name"), lead.get("email"),
                      lead.get("source"), lead.get("niche")))
    except Exception:
        note = None
    if note and str(note).strip():
        return str(note).strip().splitlines()[0][:240]
    return "Fresh %s lead in Winnipeg/Manitoba; verify on own site before outreach." % lead.get("niche", "")


def run(ctx, task=None):
    task = task or {}
    ctx = ctx or {}
    business = business_info(ctx.get("business") or {})
    slug = business["slug"]
    niche = task.get("niche") or "business services"
    region = task.get("region") or "Winnipeg, Manitoba"
    out_dir = ctx.get("out_dir") or os.path.join(
        os.path.expanduser("~/workspace/ai-agency"), "runs")
    today = date.today().isoformat()
    log = []
    held = []

    found = []
    # (a) web search
    if not task.get("skip_web") and web_search is not None:
        queries = [
            "%s %s" % (niche, region),
            "%s %s business directory" % (niche, region),
        ]
        for q in queries:
            try:
                results = web_search(q, max_results=5)
            except Exception as exc:
                log.append("web search failed for %r: %s" % (q, exc))
                continue
            for r in results:
                email = ""
                m = EMAIL_RE.search(r.get("snippet", "") or "")
                if m:
                    email = m.group(0)
                if not email:
                    continue  # no published email, cannot outreach
                found.append({"name": r.get("name", ""),
                              "email": email,
                              "source": r.get("url", ""),
                              "source_tag": _tag_source(r.get("url", ""), niche),
                              "niche": niche})
        log.append("web search: %d prospects with email" % len(found))
    else:
        log.append("web search skipped")

    # (b) local lead file
    local_path = task.get("local_leads_file") or os.path.join(LEADS_DIR, slug + ".md")
    local = _parse_local_leads(local_path)
    for entry in local:
        found.append({"name": entry["name"],
                      "email": entry["email"],
                      "source": entry.get("source") or local_path,
                      "source_tag": _tag_source(entry.get("source", ""), niche),
                      "niche": niche})
    log.append("local file %s: %d leads" % (local_path, len(local)))

    # (c) dedupe + rules
    used = load_used_emails(path=task.get("used_emails_path"),
                            extra=task.get("used_emails"))
    state_leads = state_get(ctx.get("state"), "leads", []) or []
    state_emails = {str(l.get("email", "")).lower()
                    for l in state_leads if isinstance(l, dict)}
    new_leads, seen = [], set()
    n_dup = n_generic = n_bnb_drop = 0
    n_qualified = 0  # Bro calls are slow (~10s each); cap at 3 per run.
    for lead in found:
        email = (lead.get("email") or "").strip().lower()
        lead["email"] = email
        if not email or "@" not in email:
            continue
        if email in seen:
            n_dup += 1
            continue
        seen.add(email)
        blocked, reason = is_blocked_recipient(email, lead.get("name", ""))
        if blocked:
            n_generic += 1
            held.append("%s: %s" % (email, reason))
            continue
        if email in used or email in state_emails:
            n_dup += 1
            continue
        if slug == "bnb-media" and lead.get("source_tag") != "listing":
            n_bnb_drop += 1
            continue
        if n_qualified < 3:
            lead["qualification"] = _qualify_note(ctx, lead)
            n_qualified += 1
        else:
            lead["qualification"] = (
                "Fresh %s lead in Winnipeg/Manitoba; verify on own site "
                "before outreach." % lead.get("niche", ""))
        new_leads.append(lead)

    # (e) write output + state (out_dir is already runs/<date>)
    runs_dir = ensure_dir(out_dir)
    out_path = os.path.join(runs_dir, "leads-%s.json" % slug)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(new_leads, fh, indent=2)
    state_set(ctx.get("state"), "leads",
              list(state_leads) + new_leads)
    state_log(ctx.get("state"), "lead_researcher %s: %d new leads" % (slug, len(new_leads)))
    summary = ("lead_researcher %s: found %d, new %d, held/dropped %d "
               "(dup %d, generic/blocked %d, bnb-nonlisting %d)"
               % (slug, len(found), len(new_leads),
                  n_dup + n_generic + n_bnb_drop, n_dup, n_generic, n_bnb_drop))
    log.append(summary)
    return {"status": "ok", "agent": "lead_researcher",
            "outputs": [out_path], "summary": summary,
            "held": held, "log": log}
