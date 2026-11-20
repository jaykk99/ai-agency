"""Invoicing tracker agent (shared, finance).

run(ctx, task) -> dict per the agent interface.
Tracks every invoice from quote to paid: what is owed, what is
overdue, and what needs chasing.

task carries:
  {"invoices": [{id, customer, amount, due_date, status, quote_id?}]}
Invoices may also come from state key "invoices" when the task does
not carry them. Status values: "paid", "unpaid", "overdue",
"disputed", anything else counts as open.

Writes runs/<date>/invoices-<slug>.json with totals, the overdue
list, the due-soon list, and disputed invoices. Disputed invoices
and missing payments escalate to Jay (status "held").
Dry-run only: tracks numbers, never charges or refunds anyone.
"""
import datetime

from agents._bcommon import (
    make_result, run_date, run_dir, slug_of, state_get, state_log,
    write_json,
)

DUE_SOON_DAYS = 7


def _money(value):
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def _business_of(invoice):
    business = invoice.get("business")
    if isinstance(business, dict):
        return str(business.get("slug") or "")
    return str(business or "")


def classify_invoices(invoices, slug, today):
    """Split invoices into buckets. Returns (rows, problems)."""
    rows = []
    problems = []
    for i, inv in enumerate(invoices):
        label = "invoice %d" % (i + 1)
        if not isinstance(inv, dict):
            problems.append("%s is not an object" % label)
            continue
        amount = _money(inv.get("amount"))
        if amount is None:
            problems.append("%s has no numeric amount" % label)
            continue
        due = _parse_date(inv.get("due_date"))
        status = str(inv.get("status") or "unpaid").strip().lower()
        row = {
            "id": str(inv.get("id") or "?"),
            "customer": str(inv.get("customer") or "").strip(),
            "amount": amount,
            "due_date": due.isoformat() if due else None,
            "status": status,
            "quote_id": (str(inv.get("quote_id")).strip()
                         if inv.get("quote_id") else None),
        }
        if status == "paid":
            row["bucket"] = "paid"
        elif status == "disputed":
            row["bucket"] = "disputed"
        elif due is not None and due < today:
            row["bucket"] = "overdue"
        elif due is not None and (due - today).days <= DUE_SOON_DAYS:
            row["bucket"] = "due_soon"
        else:
            row["bucket"] = "open"
        rows.append(row)
    return rows, problems


def run(ctx, task):
    task = dict(task or {})
    slug = slug_of(ctx)
    date = run_date(task)
    today = _parse_date(date) or datetime.date.today()
    directory = run_dir(ctx, task)
    log = []

    invoices = task.get("invoices")
    if invoices is None:
        invoices = state_get(ctx, "invoices")
    if invoices is None:
        return make_result(
            "invoicing_tracker", "held", [],
            "No invoices provided for %s: give invoices in the task or "
            "store them under state key 'invoices'." % slug,
            ["no invoices provided"], log)
    if not isinstance(invoices, list):
        return make_result(
            "invoicing_tracker", "held", [],
            "Invoices for %s are not a list." % slug,
            ["invoices is not a list"], log)

    if slug != "shared":
        invoices = [inv for inv in invoices
                    if isinstance(inv, dict)
                    and _business_of(inv) in ("", slug)]

    rows, problems = classify_invoices(invoices, slug, today)
    owed = round(sum(r["amount"] for r in rows
                     if r["bucket"] in ("open", "due_soon", "overdue",
                                        "disputed")), 2)
    overdue = [r for r in rows if r["bucket"] == "overdue"]
    overdue_total = round(sum(r["amount"] for r in overdue), 2)
    due_soon = [r for r in rows if r["bucket"] == "due_soon"]
    disputed = [r for r in rows if r["bucket"] == "disputed"]
    paid_total = round(sum(r["amount"] for r in rows
                           if r["bucket"] == "paid"), 2)

    report = {
        "business": slug,
        "date": date,
        "total_owed": owed,
        "paid_total": paid_total,
        "overdue_total": overdue_total,
        "overdue": overdue,
        "due_soon": due_soon,
        "disputed": disputed,
        "invoices": rows,
        "problems": problems,
    }
    path = write_json(
        "%s/invoices-%s.json" % (directory, slug), report)
    state_log(ctx, "invoicing_tracker: %s owed $%.2f, overdue $%.2f "
                   "(%d), disputed %d" % (slug, owed, overdue_total,
                                          len(overdue), len(disputed)))
    log.append("wrote %s" % path)

    held = list(problems)
    held += ["invoice %s ($%.2f) overdue since %s"
             % (r["id"], r["amount"], r["due_date"]) for r in overdue]
    held += ["invoice %s ($%.2f) is disputed, needs Jay"
             % (r["id"], r["amount"]) for r in disputed]
    summary = ("Invoices for %s on %s: $%.2f owed, $%.2f paid, "
               "$%.2f overdue (%d), %d due soon, %d disputed."
               % (slug, date, owed, paid_total, overdue_total,
                  len(overdue), len(due_soon), len(disputed)))
    status = "held" if held else "ok"
    return make_result("invoicing_tracker", status, [path], summary,
                       held, log)
