"""Bookkeeper agent (shared, finance).

run(ctx, task) -> dict per the agent interface.
Keeps the books straight per business: records income and expense
entries and reconciles them against invoices.

task carries:
  {"entries": [{date, type: "income"|"expense", amount, description,
                invoice_id (optional)}],
   "invoices": [{id, amount, status}] (optional, for reconciliation)}
Entries/invoices may also come from state keys "ledger_entries" /
"invoices" when the task does not carry them.

Writes runs/<date>/ledger-<slug>.json with the validated entries,
totals, balance, and any reconciliation mismatches. Validated entries
are stored back to state ("ledger_entries", "ledger_balance").
Dry-run only: records numbers, never moves money.
"""
import datetime

from agents._bcommon import (
    make_result, run_date, run_dir, slug_of, state_get, state_log,
    state_set, write_json,
)

ENTRY_TYPES = ("income", "expense")


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


def validate_entries(entries):
    """Return (valid, problems). valid entries are normalised dicts."""
    valid = []
    problems = []
    if not isinstance(entries, list):
        return [], ["entries is not a list"]
    for i, entry in enumerate(entries):
        label = "entry %d" % (i + 1)
        if not isinstance(entry, dict):
            problems.append("%s is not an object" % label)
            continue
        amount = _money(entry.get("amount"))
        if amount is None:
            problems.append("%s has no numeric amount" % label)
            continue
        if amount <= 0:
            problems.append("%s amount must be > 0 (got %s)"
                            % (label, entry.get("amount")))
            continue
        etype = str(entry.get("type") or "").strip().lower()
        if etype not in ENTRY_TYPES:
            problems.append("%s type must be income or expense (got %r)"
                            % (label, entry.get("type")))
            continue
        date = _parse_date(entry.get("date"))
        if date is None:
            problems.append("%s has no valid date" % label)
            continue
        valid.append({
            "date": date.isoformat(),
            "type": etype,
            "amount": amount,
            "description": str(entry.get("description") or "").strip(),
            "invoice_id": (str(entry.get("invoice_id")).strip()
                           if entry.get("invoice_id") else None),
        })
    return valid, problems


def reconcile(entries, invoices):
    """Match paid invoices to income entries and vice versa.

    Returns a list of mismatch strings. An income entry with an
    invoice_id must match that invoice's amount; a paid invoice with
    no matching income entry (by invoice_id or by exact amount) is
    flagged as unrecorded income.
    """
    mismatches = []
    invoices = [inv for inv in (invoices or []) if isinstance(inv, dict)]
    by_id = {}
    for inv in invoices:
        inv_id = str(inv.get("id") or "").strip()
        if inv_id:
            by_id[inv_id] = inv
    income_by_invoice = {}
    unmatched_income = []
    for entry in entries:
        if entry["type"] != "income":
            continue
        inv_id = entry.get("invoice_id")
        if inv_id and inv_id in by_id:
            income_by_invoice.setdefault(inv_id, []).append(entry)
        elif inv_id:
            unmatched_income.append(
                "income $%.2f on %s references unknown invoice %s"
                % (entry["amount"], entry["date"], inv_id))
        # Income with no invoice_id is matched by amount below.
    income_amounts = {}
    for entry in entries:
        if entry["type"] == "income" and not entry.get("invoice_id"):
            income_amounts[entry["amount"]] = (
                income_amounts.get(entry["amount"], 0) + 1)
    for inv in invoices:
        if str(inv.get("status") or "").strip().lower() != "paid":
            continue
        inv_id = str(inv.get("id") or "").strip()
        amount = _money(inv.get("amount"))
        if amount is None:
            mismatches.append("invoice %s has no numeric amount"
                              % (inv_id or "?"))
            continue
        matched = income_by_invoice.get(inv_id)
        if matched:
            recorded = round(sum(e["amount"] for e in matched), 2)
            if recorded != amount:
                mismatches.append(
                    "invoice %s ($%.2f) recorded as $%.2f in the ledger"
                    % (inv_id, amount, recorded))
            continue
        if income_amounts.get(amount, 0) > 0:
            income_amounts[amount] -= 1
            continue
        mismatches.append(
            "paid invoice %s ($%.2f) has no matching income entry"
            % (inv_id or "?", amount))
    mismatches.extend(unmatched_income)
    return mismatches


def run(ctx, task):
    task = dict(task or {})
    slug = slug_of(ctx)
    date = run_date(task)
    directory = run_dir(ctx, task)
    log = []

    entries = task.get("entries")
    if entries is None:
        entries = state_get(ctx, "ledger_entries")
    invoices = task.get("invoices")
    if invoices is None:
        invoices = state_get(ctx, "invoices")
    if entries is None:
        return make_result(
            "bookkeeper", "held", [],
            "No ledger entries provided for %s: give entries in the task "
            "or store them under state key 'ledger_entries'." % slug,
            ["no ledger entries provided"], log)

    valid, problems = validate_entries(entries)
    income = round(sum(e["amount"] for e in valid
                       if e["type"] == "income"), 2)
    expense = round(sum(e["amount"] for e in valid
                        if e["type"] == "expense"), 2)
    balance = round(income - expense, 2)
    mismatches = reconcile(valid, invoices or [])
    reconciled = not problems and not mismatches

    ledger = {
        "business": slug,
        "date": date,
        "entries": valid,
        "income_total": income,
        "expense_total": expense,
        "balance": balance,
        "problems": problems,
        "mismatches": mismatches,
        "reconciled": reconciled,
    }
    path = write_json(
        "%s/ledger-%s.json" % (directory, slug), ledger)
    state_set(ctx, "ledger_entries", valid)
    state_set(ctx, "ledger_balance", balance)
    state_log(ctx, "bookkeeper: %s balance $%.2f (%d entries, %d problems, "
                   "%d mismatches)" % (slug, balance, len(valid),
                                      len(problems), len(mismatches)))
    log.append("wrote %s" % path)

    held = list(problems) + list(mismatches)
    if held:
        summary = ("Books for %s on %s do NOT reconcile: income $%.2f, "
                   "expenses $%.2f, balance $%.2f, %d problem(s)."
                   % (slug, date, income, expense, balance, len(held)))
        return make_result("bookkeeper", "held", [path], summary, held, log)
    summary = ("Books for %s on %s reconcile: income $%.2f, expenses "
               "$%.2f, balance $%.2f across %d entries."
               % (slug, date, income, expense, balance, len(valid)))
    return make_result("bookkeeper", "ok", [path], summary, [], log)
