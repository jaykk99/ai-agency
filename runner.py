"""CLI runner for the 50-agent AI agency.

Usage:
    python3 runner.py --agent lead_researcher --business gutter-genie
    python3 runner.py --all [--date 2026-11-16]
    python3 runner.py --agent outreach_sender --business bnb-media --live

Dry-run is the default. --live is REFUSED unless
approvals/live-approved.txt exists in the repo root; in v1 it never
exists, so live always refuses (exit 2).

Exit codes: 0 = all runs ok / planned / held, 1 = any run errored,
2 = live refused or usage error.
"""

import argparse
import sys
from datetime import date as _date
from pathlib import Path

ROOT = Path(__file__).resolve().parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from supervisor import Supervisor  # noqa: E402

# Daily sequence, run in this order. Registry order breaks ties.
# Shared agents (ops, finance, quality, ...) run once per business
# in --all mode; per-business agents run once for their own business.
DAILY_SEQUENCE = [
    "ops_manager",
    "lead_source_hunter",
    "lead_researcher",
    "outreach_sender",
    "followup_chaser",
    "customer_support",
    "review_requester",
    "bnb_video_producer",
    "quality_control",
    "social_creator",
    "ads_manager",
    "finance_controller",
    "bookkeeper",
    "invoicing_tracker",
    "reporting",
]


def _print_result(result):
    agent = result.get("agent")
    status = result.get("status")
    summary = result.get("summary") or ""
    outputs = result.get("outputs") or []
    held = result.get("held") or []
    line = "[%s] %s: %s" % (status, agent, summary)
    print(line)
    for path in outputs:
        print("    output: %s" % path)
    for reason in held:
        print("    held: %s" % reason)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run AI agency agents (dry-run by default).")
    parser.add_argument("--agent", help="agent id to run")
    parser.add_argument("--business", help="business slug")
    parser.add_argument("--all", action="store_true",
                        help="run the daily sequence for every business")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="dry run (default)")
    parser.add_argument("--live", action="store_true",
                        help="live mode; refused unless approvals/live-approved.txt exists")
    parser.add_argument("--date", default=None,
                        help="run date YYYY-MM-DD (default: today)")
    parser.add_argument("--root", default=str(ROOT),
                        help="repo root (default: runner location)")
    args = parser.parse_args(argv)

    root = Path(args.root)
    date_str = args.date or _date.today().isoformat()
    dry_run = True

    if args.live:
        approval = root / "approvals" / "live-approved.txt"
        if not approval.exists():
            print("LIVE REFUSED: approvals/live-approved.txt does not exist.")
            print("Live sends are disabled in v1. Re-run without --live "
                  "(dry-run).")
            return 2
        dry_run = False

    supervisor = Supervisor(root=root)
    results = []

    if args.all:
        try:
            registry = supervisor.load_registry()
            businesses = supervisor.load_businesses()
        except FileNotFoundError as exc:
            print("error: %s" % exc)
            return 1
        except Exception as exc:
            print("error: could not load config: %s" % exc)
            return 1
        by_id = {e.get("id"): e for e in registry
                 if isinstance(e, dict) and e.get("id")}
        # Daily sequence, in order. Registry ids may be per-business
        # (e.g. gg-lead-researcher), so match exact ids and "-<name>"
        # suffixes, normalising "_" to "-" (sequence names use
        # underscores, registry ids use hyphens). If no sequence agent
        # matches at all (different registry shape), fall back to every
        # working agent in order.
        ordered = []
        for name in DAILY_SEQUENCE:
            norm = name.replace("_", "-")
            for entry in registry:
                if not isinstance(entry, dict):
                    continue
                eid = (entry.get("id") or "").replace("_", "-")
                if eid == norm or eid.endswith("-" + norm):
                    ordered.append(entry)
        if not ordered:
            ordered = [e for e in registry
                       if isinstance(e, dict) and e.get("id")]
        for agent in ordered:
            agent_business = agent.get("business")
            if agent_business and agent_business != "shared":
                targets = [agent_business]
            else:
                targets = [b.get("slug") for b in businesses
                           if isinstance(b, dict) and b.get("slug")]
            for slug in targets:
                result = supervisor.run(
                    {"agent": agent.get("id"), "business": slug,
                     "task": {"mode": "daily"}},
                    dry_run=dry_run, date=date_str)
                _print_result(result)
                results.append(result)
    else:
        if not args.agent:
            parser.error("--agent is required (or use --all)")
        business = args.business
        if not business:
            # Default to the agent's own business when it is a real slug.
            try:
                agent = supervisor.find_agent(args.agent)
            except Exception as exc:
                print("error: %s" % exc)
                return 1
            if agent is None:
                print("error: unknown agent id: %s" % args.agent)
                return 1
            agent_business = agent.get("business")
            business = agent_business if agent_business not in (None, "shared") \
                else "shared"
        result = supervisor.run(
            {"agent": args.agent, "business": business,
             "task": {"mode": "single"}},
            dry_run=dry_run, date=date_str)
        _print_result(result)
        results.append(result)

    if any(r.get("status") == "error" for r in results):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
