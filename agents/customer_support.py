"""Customer support agent (shared module, one per business).

run(ctx, task) -> dict per the agent interface. Backs the five
per-business customer-support registry entries (gg, www, mm, bnb,
db). Answers customer questions and keeps jobs moving after the
quote is accepted.

task carries:
  {"messages": [{from, subject, body, date?}]}
  or a single {"message": ..., "from": ..., "subject": ...}
  {"jobs": [{customer, email, job, status, ...}]} (optional, for
  matching a message to a job record)

Each message is classified: complaint, dispute, out-of-scope, or
question. A short plain reply is drafted (dry-run only, never
sent), guard-checked, and written to runs/<date>/support-<slug>.json.
Complaints, price disputes, refund asks, and out-of-scope work
escalate to Jay and are held, never auto-answered as final.
Replies are short and plain with no em dash and no AI label; the
phone goes on every reply except bnb-media (no phone in copy).
"""
from agents._bcommon import (
    ctx_guard_check, has_ai_label, make_result, run_date, run_dir,
    slug_of, state_log, write_json,
)

PHONE = "204-972-0325"
EM_DASH = "\u2014"

COMPLAINT_WORDS = ("complain", "unhappy", "terrible", "awful", "horrible",
                   "damage", "damaged", "ruined", "late", "never showed",
                   "no-show", "angry", "furious", "disappointed", "bad job",
                   "mess")
DISPUTE_WORDS = ("dispute", "overcharged", "too much", "wrong price",
                 "refund", "chargeback", "did not agree", "never agreed")
OUT_OF_SCOPE_WORDS = ("roof replacement", "new roof", "siding",
                      "window replacement", "paint the house",
                      "plumbing", "electrical work", "hvac")
QUESTION_WORDS = ("when", "how much", "what time", "available",
                  "question", "can you", "do you")


def classify(body, subject=""):
    """Return (classification, reason)."""
    text = ((subject or "") + " " + (body or "")).lower()
    for word in COMPLAINT_WORDS:
        if word in text:
            return "complaint", "mentions %r" % word
    for word in DISPUTE_WORDS:
        if word in text:
            return "dispute", "mentions %r" % word
    for word in OUT_OF_SCOPE_WORDS:
        if word in text:
            return "out-of-scope", "asks about %r" % word
    return "question", "general question"


def _match_job(message, jobs):
    sender = (message.get("from") or "").strip().lower()
    for job in jobs or []:
        if not isinstance(job, dict):
            continue
        if sender and sender == str(job.get("email") or "").strip().lower():
            return job
        if sender and sender == str(job.get("customer") or "").strip().lower():
            return job
    return None


def draft_reply(slug, business_name, classification, message, job):
    """Draft a short plain reply. Never claims a resolution for
    complaints/disputes; those go to Jay."""
    sender = (message.get("from") or "there").strip()
    if classification == "complaint":
        body = ("Hi %s, thanks for letting us know. I am sorry the work "
                "was not right. I have passed this to Jay to look at "
                "personally and he will be in touch about next steps."
                % sender)
    elif classification == "dispute":
        body = ("Hi %s, thanks for flagging the price concern. I have "
                "passed this to Jay to review the quote and the charge "
                "with you directly." % sender)
    elif classification == "out-of-scope":
        body = ("Hi %s, thanks for asking. That work is outside what %s "
                "does, so we cannot take it on. Happy to help with our "
                "usual services instead." % (sender, business_name))
    else:
        job_line = ""
        if job:
            job_line = (" Your job (%s) is %s."
                        % (job.get("job") or "on file",
                           job.get("status") or "in progress"))
        body = ("Hi %s, thanks for your message.%s Let us know if you "
                "need anything else and we will get back to you."
                % (sender, job_line))
    if slug != "bnb-media":
        body += " Call or text %s." % PHONE
    return body


def _clean(text):
    return text.replace(EM_DASH, "-").strip()


def run(ctx, task):
    task = dict(task or {})
    slug = slug_of(ctx)
    date = run_date(task)
    directory = run_dir(ctx, task)
    log = []
    business = ctx.get("business") or {}
    business_name = (business.get("name") if isinstance(business, dict)
                     else None) or slug.replace("-", " ").title()

    messages = task.get("messages")
    if messages is None:
        if task.get("message") is not None:
            messages = [{
                "from": task.get("from") or "",
                "subject": task.get("subject") or "",
                "body": task.get("message") or "",
            }]
        else:
            messages = []
    if isinstance(messages, dict):
        messages = [messages]
    if not isinstance(messages, list):
        return make_result(
            "customer_support", "held", [],
            "Messages for %s are not a list." % slug,
            ["messages is not a list"], log)
    jobs = task.get("jobs") or []

    replies = []
    escalations = []
    held = []
    for item in messages:
        if not isinstance(item, dict):
            held.append("a message is not an object")
            continue
        body = str(item.get("body") or item.get("message") or "")
        subject = str(item.get("subject") or "")
        if not body.strip():
            held.append("a message has no body")
            continue
        classification, reason = classify(body, subject)
        job = _match_job(item, jobs)
        draft = _clean(draft_reply(slug, business_name, classification,
                                   item, job))
        if has_ai_label(draft):
            held.append("draft reply contains an AI-label phrase")
            continue
        allowed, guard_reasons = ctx_guard_check(ctx, {
            "to": item.get("from") or "",
            "from": "",
            "subject": "Re: %s" % subject if subject else "Re: your message",
            "body": draft,
            "channel": "email",
        })
        record = {
            "from": item.get("from") or "",
            "subject": subject,
            "classification": classification,
            "reason": reason,
            "job_matched": bool(job),
            "draft": draft,
            "guard_passed": allowed,
        }
        if not allowed:
            record["guard_reasons"] = list(guard_reasons)
            held.append("guard rejected reply to %s: %s"
                        % (item.get("from") or "?",
                           "; ".join(guard_reasons)))
        if classification in ("complaint", "dispute", "out-of-scope"):
            record["escalated"] = True
            escalations.append(
                "%s from %s escalated to Jay (%s)"
                % (classification, item.get("from") or "?",
                   record["reason"]))
        replies.append(record)

    if not messages:
        return make_result(
            "customer_support", "held", [],
            "No customer messages to answer for %s." % slug,
            ["no customer messages provided"], log)

    report = {
        "business": slug,
        "date": date,
        "replies": replies,
        "escalations": escalations,
    }
    path = write_json(
        "%s/support-%s.json" % (directory, slug), report)
    state_log(ctx, "customer_support: %s answered %d message(s), %d "
                   "escalation(s)" % (slug, len(replies), len(escalations)))
    log.append("wrote %s" % path)
    held.extend(escalations)
    summary = ("Customer support for %s: %d reply draft(s), %d "
               "escalation(s) to Jay." % (slug, len(replies),
                                         len(escalations)))
    status = "held" if held else "ok"
    return make_result("customer_support", status, [path], summary,
                       held, log)
