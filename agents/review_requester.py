"""GG review requester agent (gutter-genie, reviews).

run(ctx, task) -> dict per the agent interface.
Asks happy Gutter Genie customers for a review after the job is
done and paid.

task carries:
  {"jobs": [{customer, email, job, paid: bool, happy: bool, date?}]}
Jobs may also come from state key "completed_jobs".

Only jobs that are paid AND happy get a review request draft
(guard-checked, phone included, no AI label). Unhappy customers
are never asked: they escalate to Jay instead. Unpaid jobs are
skipped until paid. Writes runs/<date>/review-requests-<slug>.json.
Dry-run only: drafts requests, never sends them.
"""
from agents._bcommon import (
    ctx_guard_check, make_result, run_date, run_dir, slug_of,
    state_get, state_log, write_json,
)

PHONE = "204-972-0325"


def _is_true(value):
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("true", "yes", "1", "paid")


def run(ctx, task):
    task = dict(task or {})
    slug = slug_of(ctx)
    date = run_date(task)
    directory = run_dir(ctx, task)
    log = []

    jobs = task.get("jobs")
    if jobs is None:
        jobs = state_get(ctx, "completed_jobs")
    if jobs is None:
        return make_result(
            "review_requester", "held", [],
            "No completed jobs provided for %s: give jobs in the task "
            "or store them under state key 'completed_jobs'." % slug,
            ["no completed jobs provided"], log)
    if not isinstance(jobs, list):
        return make_result(
            "review_requester", "held", [],
            "Jobs for %s are not a list." % slug,
            ["jobs is not a list"], log)

    requests = []
    skipped = []
    escalations = []
    held = []
    for item in jobs:
        if not isinstance(item, dict):
            held.append("a job is not an object")
            continue
        customer = str(item.get("customer") or "").strip()
        email = str(item.get("email") or "").strip()
        job_desc = str(item.get("job") or "gutter work").strip()
        paid = _is_true(item.get("paid"))
        happy = _is_true(item.get("happy"))
        if not customer or not email:
            held.append("a job is missing a customer name or email")
            continue
        if not happy:
            escalations.append(
                "%s was unhappy after %s; do NOT ask for a review"
                % (customer, job_desc))
            continue
        if not paid:
            skipped.append("%s (%s) not paid yet; review request held "
                           "until paid" % (customer, job_desc))
            continue
        body = ("Hi %s, thanks for choosing Gutter Genie for %s. Glad "
                "it went well. If you have a minute, a Google review "
                "would mean a lot. Call or text %s with anything else."
                % (customer, job_desc, PHONE))
        allowed, guard_reasons = ctx_guard_check(ctx, {
            "to": email,
            "from": "guttergeniewpg@gmail.com",
            "subject": "How did we do?",
            "body": body,
            "channel": "email",
        })
        record = {"customer": customer, "email": email, "job": job_desc,
                  "draft": body, "guard_passed": allowed}
        if not allowed:
            record["guard_reasons"] = list(guard_reasons)
            held.append("guard rejected review request to %s: %s"
                        % (email, "; ".join(guard_reasons)))
        requests.append(record)

    report = {
        "business": slug,
        "date": date,
        "requests": requests,
        "skipped": skipped,
        "escalations": escalations,
    }
    path = write_json(
        "%s/review-requests-%s.json" % (directory, slug), report)
    state_log(ctx, "review_requester: %s %d request(s), %d skipped, "
                   "%d escalation(s)"
                   % (slug, len(requests), len(skipped), len(escalations)))
    log.append("wrote %s" % path)
    held.extend(escalations)
    summary = ("Review requests for %s: %d drafted, %d skipped "
               "(unpaid), %d unhappy customer(s) escalated, never asked."
               % (slug, len(requests), len(skipped), len(escalations)))
    status = "held" if held else "ok"
    return make_result("review_requester", status, [path], summary,
                       held, log)
