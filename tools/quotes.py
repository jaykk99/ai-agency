"""Pure quote math and formatting for the ai-agency (set B, worker W4).

Stdlib only, no I/O. Rate card is Gutter Genie's, from Jay's standing memory:
  cleaning $1.75/ft, base fee $100, downspout flush $15 each,
  guard install $8.50/ft, 3-storey cleaning $2.63/ft (+50%),
  travel $45 Steinbach / $55 Grunthal. No tax.
"""

CLEANING_RATE = 1.75
CLEANING_RATE_3_STOREY = 2.63
BASE_FEE = 100.0
FLUSH_RATE = 15.0
GUARD_RATE = 8.50
TRAVEL_RATES = {"steinbach": 45.0, "grunthal": 55.0}
DEPOSIT_RATE = 0.30
PHONE = "204-972-0325"


def _money(amount):
    return round(float(amount), 2)


def gutter_quote(feet, downspouts=0, guards_feet=0, storeys=2, travel=""):
    """Build a Gutter Genie quote from measurements.

    Returns a dict with line_items (list of {label, amount}), total,
    deposit (30%), and a breakdown list of (label, amount) tuples.
    Raises ValueError on negative inputs or unknown travel location.
    """
    feet = float(feet)
    downspouts = int(downspouts)
    guards_feet = float(guards_feet)
    storeys = int(storeys)
    if feet < 0 or downspouts < 0 or guards_feet < 0 or storeys < 1:
        raise ValueError("feet, downspouts, guards_feet must be >= 0 and storeys >= 1")
    travel_key = (travel or "").strip().lower()
    if travel_key and travel_key not in TRAVEL_RATES:
        raise ValueError("unknown travel location: %r (use Steinbach or Grunthal)" % travel)

    rate = CLEANING_RATE_3_STOREY if storeys >= 3 else CLEANING_RATE
    line_items = []
    if feet > 0:
        line_items.append({
            "label": "Gutter cleaning (%s ft @ $%.2f/ft)" % (_fmt_num(feet), rate),
            "amount": _money(feet * rate),
        })
    has_job = feet > 0 or downspouts > 0 or guards_feet > 0
    if has_job:
        line_items.append({"label": "Base fee", "amount": _money(BASE_FEE)})
    if downspouts > 0:
        line_items.append({
            "label": "Downspout flush (%d @ $%.2f each)" % (downspouts, FLUSH_RATE),
            "amount": _money(downspouts * FLUSH_RATE),
        })
    if guards_feet > 0:
        line_items.append({
            "label": "Guard install (%s ft @ $%.2f/ft)" % (_fmt_num(guards_feet), GUARD_RATE),
            "amount": _money(guards_feet * GUARD_RATE),
        })
    if travel_key:
        line_items.append({
            "label": "Travel (%s)" % travel_key.capitalize(),
            "amount": _money(TRAVEL_RATES[travel_key]),
        })
    total = _money(sum(item["amount"] for item in line_items))
    return {
        "feet": feet,
        "downspouts": downspouts,
        "guards_feet": guards_feet,
        "storeys": storeys,
        "travel": travel_key,
        "cleaning_rate": rate,
        "line_items": line_items,
        "breakdown": [(item["label"], item["amount"]) for item in line_items],
        "total": total,
        "deposit": _money(total * DEPOSIT_RATE),
    }


def _fmt_num(value):
    if float(value) == int(value):
        return str(int(value))
    return str(value)


def format_quote(business_slug, scope_items, breakdown, total, note=""):
    """Format a quote as text.

    Gutter Genie uses Jay's exact quote format:
      1) numbered JOB SCOPE 2) optional italic note 3) ESTIMATE BREAKDOWN
      with dashed total 4) ESTIMATED TOTAL 5) PAYMENT TERMS: 30% deposit,
      balance on completion 6) closing line asking to confirm and schedule.
    No tax. Other businesses get a plain scope + total format.
    """
    slug = (business_slug or "").strip().lower()
    scope = list(scope_items or [])
    lines = []
    total = _money(total)
    deposit = _money(total * DEPOSIT_RATE)
    if slug == "gutter-genie":
        lines.append("GUTTER GENIE - ESTIMATE")
        lines.append("")
        lines.append("JOB SCOPE")
        if scope:
            for i, item in enumerate(scope, 1):
                lines.append("%d. %s" % (i, item))
        else:
            lines.append("1. Gutter cleaning and downspout service as discussed.")
        if note:
            lines.append("")
            lines.append("*%s*" % note)
        lines.append("")
        lines.append("ESTIMATE BREAKDOWN")
        for label, amount in breakdown:
            lines.append("%s: $%.2f" % (label, _money(amount)))
        lines.append("-" * 30)
        lines.append("ESTIMATED TOTAL: $%.2f" % total)
        lines.append("")
        lines.append("PAYMENT TERMS: 30%% deposit ($%.2f), balance on completion. No tax."
                     % deposit)
        lines.append("")
        lines.append("Please confirm and we will schedule your visit at 12:30 PM on a weekday.")
        lines.append("Call or text %s with any questions." % PHONE)
    else:
        lines.append("ESTIMATE")
        lines.append("")
        lines.append("SCOPE")
        if scope:
            for i, item in enumerate(scope, 1):
                lines.append("%d. %s" % (i, item))
        if note:
            lines.append("")
            lines.append("*%s*" % note)
        lines.append("")
        lines.append("ESTIMATED TOTAL: $%.2f" % total)
        lines.append("")
        lines.append("Please confirm and we will schedule next steps.")
    return "\n".join(lines) + "\n"
