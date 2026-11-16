"""Shared helpers for set-A agents (lead research, outreach, follow-up).

Kept stdlib-only. The guard contract: ctx["guard"] is a callable taking a
draft dict {to, from, subject, body} and returning one of:
  - bool (True = allowed)
  - (bool, [reasons])
  - dict with "ok"/"allowed"/"passed" bool and optional "reasons" list
Anything else / an exception is treated as a hold.
"""

import os
import re

BUSINESSES = {
    "gutter-genie": {
        "slug": "gutter-genie", "name": "Gutter Genie",
        "email": "guttergeniewpg@gmail.com", "provider": "gmail",
        "gmail_account_id": "18cde8eac8a445de9d66dc42144fffce",
        "phone": "204-972-0325", "site": "guttergenie.pages.dev",
    },
    "winnipeg-web-works": {
        "slug": "winnipeg-web-works", "name": "Winnipeg Web Works",
        "email": "winnipegwebworks@gmail.com", "provider": "gmail",
        "gmail_account_id": "4b5e7b2ffcc843fcac0a7c45906d4c7d",
        "phone": "204-972-0325", "site": "winnipeg-web-works.pages.dev",
    },
    "move-and-metal": {
        "slug": "move-and-metal", "name": "Move & Metal",
        "email": "metalmoovers@hotmail.com", "provider": "outlook",
        "phone": "204-972-0325", "site": "move-and-metal-site.pages.dev",
    },
    "bnb-media": {
        "slug": "bnb-media", "name": "Bnb Media",
        "email": "bnbmediawpg@gmail.com", "provider": "gmail",
        "gmail_account_id": "a97ed5bfe4d54f86b4164d6f00ecd62d",
        "phone": None, "site": "bnb-media-site.pages.dev",
    },
    "demoboss": {
        "slug": "demoboss", "name": "DemoBoss",
        "email": "demobosswpg@hotmail.com", "provider": "outlook",
        "phone": "204-972-0325", "site": "demoboss-site.pages.dev",
    },
    "quonset-place": {
        "slug": "quonset-place", "name": "Quonset Place",
        "email": "thequonsetplace@hotmail.com", "provider": "outlook",
        "phone": "204-972-0325", "site": "quonset-place-site.pages.dev",
    },
    "drip-detailing": {
        "slug": "drip-detailing", "name": "Drip Detailing",
        "email": None, "provider": "none",
        "phone": "204-972-0325", "site": None,
    },
}

OUTLOOK_HELD_REASON = "Outlook reauth needed"
NO_INBOX_HELD_REASON = "no inbox yet"

GENERIC_LOCAL_PARTS = {
    "info", "admin", "sales", "hello", "contact", "support",
    "reception", "dispatch", "operations", "service",
}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def slugify_business(business):
    if not isinstance(business, dict):
        return "unknown"
    slug = business.get("slug") or business.get("id") or ""
    if slug:
        return str(slug).lower().replace(" ", "-")
    name = str(business.get("name", "")).lower().replace(" ", "-")
    return name or "unknown"


def business_info(business):
    """Merge ctx business dict over the known registry entry."""
    slug = slugify_business(business)
    info = dict(BUSINESSES.get(slug, {}))
    for k, v in (business or {}).items():
        if v is not None:
            info[k] = v
    info.setdefault("slug", slug)
    info.setdefault("name", slug.replace("-", " ").title())
    info.setdefault("provider", "gmail" if (info.get("email") or "").endswith("@gmail.com") else "outlook")
    return info


def is_generic_address(email):
    local = (email or "").split("@")[0].lower()
    return local in GENERIC_LOCAL_PARTS


def is_blocked_recipient(email, body=""):
    """Hard rules: no info@ (and other generic role mailboxes), never Integrity Roofing."""
    email = (email or "").lower()
    text = (email + " " + (body or "")).lower()
    if is_generic_address(email):
        return True, "generic role address (%s) not allowed" % email
    if "integrity roofing" in text or "integrityroofing" in text:
        return True, "never contact Integrity Roofing"
    return False, ""


# --- state store access (supports object-with-get/set and plain dict) ---

def state_get(state, key, default=None):
    if state is None:
        return default
    try:
        if hasattr(state, "get"):
            val = state.get(key, default)
            return default if val is None else val
        if isinstance(state, dict):
            return state.get(key, default)
    except Exception:
        return default
    return default


def state_set(state, key, value):
    if state is None:
        return
    try:
        if hasattr(state, "set"):
            state.set(key, value)
        elif isinstance(state, dict):
            state[key] = value
    except Exception:
        pass


def state_log(state, message):
    if state is None:
        return
    try:
        if hasattr(state, "append_log"):
            state.append_log(message)
    except Exception:
        pass


def load_used_emails(path=None, extra=None):
    """Set of lowercased already-used emails."""
    used = set()
    if extra:
        used.update(e.lower() for e in extra if e)
    candidates = []
    if path:
        candidates.append(path)
    else:
        candidates.append(os.path.expanduser(
            "~/workspace/daily-ops/dedupe/used-emails.txt"))
    for p in candidates:
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    e = line.strip().lower()
                    if e:
                        used.add(e)
        except OSError:
            continue
    return used


def check_guard(ctx, draft):
    """Run ctx['guard'] on a draft. Returns (allowed: bool, reasons: list)."""
    guard = ctx.get("guard") if isinstance(ctx, dict) else None
    if guard is None:
        blocked, reason = is_blocked_recipient(draft.get("to", ""), draft.get("body", ""))
        return (not blocked, [reason] if blocked else [])
    try:
        # Real guard is core.guard.check_outbound(business, recipient,
        # subject, body, channel, source_tag=None). Try that first.
        business = ctx.get("business") if isinstance(ctx, dict) else None
        result = guard(business or {}, draft.get("to", ""),
                       draft.get("subject", ""), draft.get("body", ""),
                       draft.get("channel", "email"),
                       draft.get("source_tag"))
    except TypeError:
        # Older stub guards take a single draft dict; fall back to that.
        try:
            result = guard(draft)
        except Exception as exc:  # guard failure = hold, never send
            return False, ["guard check failed: %s" % exc]
    except Exception as exc:  # guard failure = hold, never send
        return False, ["guard check failed: %s" % exc]
    if isinstance(result, bool):
        return result, [] if result else ["guard rejected draft"]
    if isinstance(result, (tuple, list)) and len(result) >= 1:
        allowed = bool(result[0])
        reasons = list(result[1]) if len(result) > 1 and result[1] else []
        if not allowed and not reasons:
            reasons = ["guard rejected draft"]
        return allowed, reasons
    if isinstance(result, dict):
        allowed = bool(result.get("ok", result.get("allowed", result.get("passed", False))))
        reasons = result.get("reasons") or result.get("held_reasons") or []
        if not allowed and not reasons:
            reasons = ["guard rejected draft"]
        return allowed, list(reasons)
    return False, ["guard returned unrecognized result"]


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    return path
