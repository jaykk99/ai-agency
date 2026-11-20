"""Ads manager agent (shared, advertising).

run(ctx, task) -> dict per the agent interface.
Plans and tracks paid promotion where allowed. Winnipeg Web Works
is organic-only and never gets paid ads.

task carries:
  {"business": slug, "platform": "facebook"|"google"|...,
   "budget": <number>, "objective": str, "duration_days": <number>}
  {"performance": [{platform, spend, impressions, clicks, ...}]}
  (optional, for a performance summary from given numbers only)
  {"launch": true} or {"action": "spend"|"launch"|...}  -> always held

A launch or any spend is never committed by this agent: it is held
for Jay's tap. A plan is written as a proposal only.
Writes runs/<date>/ad-plan-<slug>.md (and ad-performance-<slug>.json
when performance rows are given). Dry-run only: plans, never spends.
"""
from agents._bcommon import (
    make_result, run_date, run_dir, slug_of, state_log, write_json,
    write_text,
)

ORGANIC_ONLY = ("winnipeg-web-works",)
SPEND_WORDS = ("launch", "spend", "boost", "buy", "charge", "purchase")


def _money(value):
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _summarise_performance(rows):
    """Totals from given performance rows only; never invents numbers."""
    totals = {"spend": 0.0, "impressions": 0, "clicks": 0, "rows": 0}
    problems = []
    by_platform = {}
    for i, row in enumerate(rows or []):
        if not isinstance(row, dict):
            problems.append("performance row %d is not an object" % (i + 1))
            continue
        spend = _money(row.get("spend"))
        impressions = row.get("impressions")
        clicks = row.get("clicks")
        if spend is None or not isinstance(impressions, (int, float)) \
                or not isinstance(clicks, (int, float)):
            problems.append("performance row %d is missing spend / "
                            "impressions / clicks" % (i + 1))
            continue
        platform = str(row.get("platform") or "unknown").strip().lower()
        entry = by_platform.setdefault(
            platform, {"spend": 0.0, "impressions": 0, "clicks": 0})
        entry["spend"] = round(entry["spend"] + spend, 2)
        entry["impressions"] += int(impressions)
        entry["clicks"] += int(clicks)
        totals["spend"] = round(totals["spend"] + spend, 2)
        totals["impressions"] += int(impressions)
        totals["clicks"] += int(clicks)
        totals["rows"] += 1
    totals["ctr"] = (round(totals["clicks"] / totals["impressions"] * 100, 2)
                     if totals["impressions"] else None)
    totals["cpc"] = (round(totals["spend"] / totals["clicks"], 2)
                     if totals["clicks"] else None)
    for entry in by_platform.values():
        entry["ctr"] = (round(entry["clicks"] / entry["impressions"] * 100, 2)
                        if entry["impressions"] else None)
        entry["cpc"] = (round(entry["spend"] / entry["clicks"], 2)
                        if entry["clicks"] else None)
    totals["by_platform"] = by_platform
    totals["problems"] = problems
    return totals


def run(ctx, task):
    task = dict(task or {})
    slug = slug_of(ctx)
    date = run_date(task)
    directory = run_dir(ctx, task)
    log = []

    if slug in ORGANIC_ONLY:
        return make_result(
            "ads_manager", "held", [],
            "Winnipeg Web Works is organic-only: no paid ads, no ad "
            "plan. Held, not planned.",
            ["winnipeg-web-works is organic-only, no paid ads"], log)

    action = str(task.get("action") or "").strip().lower()
    if task.get("launch") or any(word in action for word in SPEND_WORDS):
        return make_result(
            "ads_manager", "held", [],
            "Ad spend / launch for %s held for Jay's tap: the ads "
            "manager plans only and never spends on its own." % slug,
            ["ad spend needs Jay's tap"], log)

    platform = str(task.get("platform") or "facebook").strip().lower()
    budget = _money(task.get("budget"))
    objective = str(task.get("objective") or "").strip()
    duration = task.get("duration_days")
    try:
        duration = int(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration = None

    lines = ["AD PLAN (PROPOSAL ONLY, NO SPEND COMMITTED) - %s" % slug.upper(),
             "",
             "Date: %s" % date,
             "Platform: %s" % platform,
             "Objective: %s" % (objective or "(not set)"),
             "Proposed budget: %s"
             % ("$%.2f" % budget if budget is not None else "(not set)"),
             "Duration: %s"
             % ("%d days" % duration if duration else "(not set)"),
             "",
             "Guardrails:",
             "- No spend is committed by this plan; Jay approves any "
             "spend or launch first.",
             "- Winnipeg Web Works never gets paid ads (organic only).",
             "- Track cost per lead; pause anything with no leads after "
             "the first budget slice is spent.",
             ""]
    plan_path = write_text(
        "%s/ad-plan-%s.md" % (directory, slug), "\n".join(lines))
    outputs = [plan_path]
    log.append("wrote %s" % plan_path)

    performance = task.get("performance")
    perf_summary = ""
    if performance is not None:
        totals = _summarise_performance(performance)
        perf_path = write_json(
            "%s/ad-performance-%s.json" % (directory, slug), totals)
        outputs.append(perf_path)
        log.append("wrote %s" % perf_path)
        perf_summary = (" Performance from %d row(s): spend $%.2f, %d "
                        "impressions, %d clicks, CTR %s%%, CPC $%s."
                        % (totals["rows"], totals["spend"],
                           totals["impressions"], totals["clicks"],
                           totals["ctr"] if totals["ctr"] is not None
                           else "n/a",
                           ("%.2f" % totals["cpc"])
                           if totals["cpc"] is not None else "n/a"))
        if totals["problems"]:
            log.extend(totals["problems"])

    state_log(ctx, "ads_manager: %s plan written for %s"
              % (platform, slug))
    summary = ("Ad plan (proposal only) for %s: %s, proposed budget %s, "
               "no spend committed.%s"
               % (slug, platform,
                  "$%.2f" % budget if budget is not None else "not set",
                  perf_summary))
    return make_result("ads_manager", "ok", outputs, summary, [], log)
