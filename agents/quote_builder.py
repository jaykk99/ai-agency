"""Quote builder agent (set B, W4).

run(ctx, task) -> dict per the agent interface.
- Gutter Genie: task carries measurements, e.g.
  {"feet": 120, "downspouts": 4, "storeys": 2, "guards_feet": 0,
   "travel": "Steinbach", "scope": [...], "note": "..."}
  Writes runs/<date>/quote-<slug>-<n>.md in Jay's exact quote format.
- Winnipeg Web Works: task carries {"audit_for": "Business Name",
  "site": url}. Builds an audit outline (what to check, findings
  placeholders). Never invents data, never claims a live check not done.
- Other businesses: task carries {"scope": [...], "total": N} for a plain
  scope + total quote.
Missing required inputs -> status "held", never invent measurements.
Dry-run only: writes files, never sends anything.
"""
import os

from agents._bcommon import (
    business_dict, make_result, run_date, run_dir, slug_of, state_get,
    state_log, state_set, write_text,
)
from tools.quotes import format_quote, gutter_quote

AUDIT_SECTIONS = (
    ("Website", [
        "Site loads, no errors, mobile friendly",
        "Services and pricing are clear on the home page",
        "Contact details and calls to action are easy to find",
        "Site speed and basic SEO tags (title, description)",
    ]),
    ("Google presence", [
        "Google Business Profile exists, claimed, correct details",
        "Business shows up for its name and main service searches",
        "Photos, hours, and service area are complete",
    ]),
    ("Reviews", [
        "Review count, average rating, and recency",
        "Owner replies to reviews (positive and negative)",
        "Review themes: what customers praise or complain about",
    ]),
    ("Socials", [
        "Profiles exist and match the business name and branding",
        "Posting cadence and engagement on recent posts",
        "Bio, links, and contact info are current",
    ]),
)


def _fmt_num(value):
    value = float(value)
    return str(int(value)) if value == int(value) else str(value)


def _next_quote_path(directory, slug):
    n = 1
    while True:
        path = os.path.join(directory, "quote-%s-%d.md" % (slug, n))
        if not os.path.exists(path):
            return path, n
        n += 1


def _audit_outline(audit_for, site):
    lines = ["WINNIPEG WEB WORKS - FREE DIGITAL CREDIBILITY AUDIT"]
    lines.append("")
    lines.append("Business: %s" % audit_for)
    if site:
        lines.append("Site: %s" % site)
    lines.append("")
    lines.append("This is an audit outline. Findings below are placeholders to be")
    lines.append("filled from a real check. No live check has been run for this audit yet.")
    for title, checks in AUDIT_SECTIONS:
        lines.append("")
        lines.append("%s" % title.upper())
        for check in checks:
            lines.append("- [ ] %s: [finding pending]" % check)
    lines.append("")
    lines.append("NEXT STEP")
    lines.append("Paid credibility engagements start at $10,000 (typically about $12,500).")
    lines.append("Please confirm if you would like the full audit findings and next steps.")
    return "\n".join(lines) + "\n"


def run(ctx, task):
    task = dict(task or {})
    slug = slug_of(ctx)
    business = business_dict(ctx)
    log = []
    date = run_date(task)
    directory = run_dir(ctx, task)

    if slug == "winnipeg-web-works":
        audit_for = (task.get("audit_for") or "").strip()
        site = (task.get("site") or "").strip()
        if not audit_for:
            held = ["audit_for (business name) is missing; cannot build an audit outline"]
            log.append("held: missing audit_for")
            return make_result("quote_builder", "held", [], "Audit held: missing business name.", held, log)
        text = _audit_outline(audit_for, site)
        path = os.path.join(directory, "audit-%s.md" % slug)
        write_text(path, text)
        state_set(ctx, "last_audit_%s" % slug, path)
        state_log(ctx, "quote_builder: audit outline written for %s" % audit_for)
        log.append("wrote %s" % path)
        return make_result("quote_builder", "ok", [path],
                           "Audit outline built for %s." % audit_for, [], log)

    if slug == "gutter-genie":
        if task.get("feet") is None:
            held = ["feet (linear feet of gutter) is missing; cannot quote without measurements"]
            log.append("held: missing feet")
            return make_result("quote_builder", "held", [], "Quote held: missing measurements.", held, log)
        try:
            quote = gutter_quote(
                feet=task["feet"],
                downspouts=task.get("downspouts", 0),
                guards_feet=task.get("guards_feet", 0),
                storeys=task.get("storeys", 2),
                travel=task.get("travel", ""),
            )
        except (ValueError, TypeError) as exc:
            held = ["invalid quote inputs: %s" % exc]
            log.append("held: %s" % exc)
            return make_result("quote_builder", "held", [], "Quote held: invalid inputs.", held, log)
        scope = task.get("scope") or []
        if not scope:
            scope = ["Gutter cleaning (%s ft)" % _fmt_num(quote["feet"])]
            if quote["downspouts"]:
                scope.append("Downspout flush (%d downspouts)" % quote["downspouts"])
            if quote["guards_feet"]:
                scope.append("Guard install (%s ft)" % _fmt_num(quote["guards_feet"]))
            if quote["travel"]:
                scope.append("Travel to %s" % quote["travel"].capitalize())
        note = task.get("note", "")
        text = format_quote(slug, scope, quote["breakdown"], quote["total"], note)
        path, n = _next_quote_path(directory, slug)
        write_text(path, text)
        state_set(ctx, "last_quote_%s" % slug, path)
        state_set(ctx, "last_quote_total_%s" % slug, quote["total"])
        state_log(ctx, "quote_builder: quote %d written for %s, total $%.2f"
                  % (n, slug, quote["total"]))
        log.append("wrote %s (total $%.2f)" % (path, quote["total"]))
        return make_result("quote_builder", "ok", [path],
                           "Quote %d built for %s: $%.2f." % (n, slug, quote["total"]), [], log)

    # Other businesses: plain scope + total quote, no invented pricing.
    scope = task.get("scope") or []
    total = task.get("total")
    if not scope or total is None:
        held = ["scope and total are both required to quote for %s" % slug]
        log.append("held: missing scope/total")
        return make_result("quote_builder", "held", [], "Quote held: missing scope or total.", held, log)
    try:
        total = float(total)
    except (ValueError, TypeError):
        return make_result("quote_builder", "held", [],
                           "Quote held: total is not a number.", ["total is not a number"], log)
    breakdown = [(label, float(amount)) for label, amount in task.get("breakdown", [])]
    text = format_quote(slug, scope, breakdown, total, task.get("note", ""))
    path, n = _next_quote_path(directory, slug)
    write_text(path, text)
    state_set(ctx, "last_quote_%s" % slug, path)
    state_log(ctx, "quote_builder: quote %d written for %s, total $%.2f" % (n, slug, total))
    log.append("wrote %s (total $%.2f)" % (path, total))
    return make_result("quote_builder", "ok", [path],
                       "Quote %d built for %s: $%.2f." % (n, slug, total), [], log)
