"""Finance controller agent (shared, finance).

run(ctx, task) -> dict per the agent interface.
Owns money discipline across the agency: tracks revenue, costs, and
margins per business and flags anything off plan.

task carries:
  {"invoices": [{id, business, amount, status}],
   "payments": [{invoice_id?, business?, amount, date?}],
   "expenses": [{amount, category?, business?, date?}],
   "targets": {"revenue_target": <number>, "margin_floor": <0-1>}}
Any of invoices/payments/expenses may also come from the matching
state keys when the task does not carry them. Revenue counts paid
invoices, falling back to payment records when no paid invoices are
given. When the run's business is not "shared", only rows for that
business are counted.

A spend, refund, or price-change request in the task is never acted
on: it is held for Jay's tap, per the registry escalation rule.
Writes runs/<date>/finance-<slug>.json with the numbers and flags.
Dry-run only: reports numbers, never moves money.
"""
from agents._bcommon import (
    make_result, run_date, run_dir, slug_of, state_get, state_log,
    write_json,
)

MONEY_ACTION_WORDS = ("spend", "refund", "price change", "price_change",
                      "charge", "purchase", "buy", "transfer")


def _money(value):
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _row_business(row):
    business = row.get("business") if isinstance(row, dict) else None
    if isinstance(business, dict):
        return str(business.get("slug") or "")
    return str(business or "")


def _in_scope(row, slug):
    if slug == "shared":
        return True
    return _row_business(row) in ("", slug)


def _sum_amounts(rows, slug):
    total = 0.0
    counted = 0
    problems = []
    for i, row in enumerate(rows or []):
        if not isinstance(row, dict) or not _in_scope(row, slug):
            continue
        amount = _money(row.get("amount"))
        if amount is None:
            problems.append("row %d has no numeric amount" % (i + 1))
            continue
        total += amount
        counted += 1
    return round(total, 2), counted, problems


def run(ctx, task):
    task = dict(task or {})
    slug = slug_of(ctx)
    date = run_date(task)
    directory = run_dir(ctx, task)
    log = []

    # Money actions are always held for Jay; never acted on.
    requested = task.get("action") or task.get("money_action") or ""
    if isinstance(requested, dict):
        requested = requested.get("type") or requested.get("action") or ""
    requested = str(requested).strip().lower()
    if any(word in requested for word in MONEY_ACTION_WORDS):
        return make_result(
            "finance_controller", "held", [],
            "Money action %r held for Jay's tap: the finance controller "
            "never spends, refunds, or changes prices on its own."
            % requested,
            ["money action held for Jay: %s" % requested], log)

    invoices = task.get("invoices")
    if invoices is None:
        invoices = state_get(ctx, "invoices")
    payments = task.get("payments")
    if payments is None:
        payments = state_get(ctx, "payments")
    expenses = task.get("expenses")
    if expenses is None:
        expenses = state_get(ctx, "expenses")
    if invoices is None and payments is None and expenses is None:
        return make_result(
            "finance_controller", "held", [],
            "No invoices, payments, or expenses provided for %s: give "
            "them in the task or store them under state keys "
            "'invoices' / 'payments' / 'expenses'." % slug,
            ["no finance data provided"], log)

    problems = []
    paid_total, paid_count, p1 = _sum_amounts(
        [inv for inv in (invoices or [])
         if isinstance(inv, dict)
         and str(inv.get("status") or "").strip().lower() == "paid"],
        slug)
    problems += p1
    if paid_count == 0:
        payments_total, payments_count, p2 = _sum_amounts(payments, slug)
        problems += p2
    else:
        payments_total, payments_count = 0.0, 0
    revenue = paid_total if paid_count else payments_total
    costs, _cost_count, p3 = _sum_amounts(expenses, slug)
    problems += p3
    profit = round(revenue - costs, 2)
    margin = round(profit / revenue, 4) if revenue else None

    targets = task.get("targets") or {}
    if not isinstance(targets, dict):
        targets = {}
    revenue_target = _money(targets.get("revenue_target"))
    margin_floor = _money(targets.get("margin_floor"))
    flags = list(problems)
    if revenue_target is not None and revenue < revenue_target:
        flags.append("revenue $%.2f is below target $%.2f"
                     % (revenue, revenue_target))
    if margin_floor is not None and margin is not None \
            and margin < margin_floor:
        flags.append("margin %.1f%% is below floor %.1f%%"
                     % (margin * 100, margin_floor * 100))
    if revenue == 0 and (invoices or payments):
        flags.append("no paid revenue recorded for %s" % slug)

    report = {
        "business": slug,
        "date": date,
        "revenue": revenue,
        "costs": costs,
        "profit": profit,
        "margin": margin,
        "paid_invoices": paid_count,
        "payments_count": payments_count,
        "revenue_target": revenue_target,
        "margin_floor": margin_floor,
        "flags": flags,
    }
    path = write_json(
        "%s/finance-%s.json" % (directory, slug), report)
    state_log(ctx, "finance_controller: %s revenue $%.2f, costs $%.2f, "
                   "profit $%.2f, %d flag(s)"
                   % (slug, revenue, costs, profit, len(flags)))
    log.append("wrote %s" % path)

    margin_text = ("%.1f%%" % (margin * 100)) if margin is not None \
        else "n/a"
    summary = ("Finance for %s on %s: revenue $%.2f, costs $%.2f, "
               "profit $%.2f, margin %s, %d flag(s)."
               % (slug, date, revenue, costs, profit, margin_text,
                  len(flags)))
    status = "held" if flags else "ok"
    return make_result("finance_controller", status, [path], summary,
                       flags, log)
