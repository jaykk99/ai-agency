"""Social creator agent (set B, W4).

run(ctx, task) -> dict per the agent interface.
task may carry {"platform": "facebook|instagram",
"format": "before-after|how-it-works|walkthrough|photo-story"}.

Rules:
- Format rotation: never repeat the business's last format (read from
  state). If the requested format equals the last one, pick the next
  format in rotation. Formats under 40 views are banned (state key
  "banned_formats") and skipped.
- Hook first line, short plain copy in Jay's voice (short, direct, no
  em dashes).
- Phone 204-972-0325 on every post except Instagram (CTA "DM this
  account" instead). Bnb Media copy never carries a phone number.
- Never put an AI label in public copy; the draft is self-checked and
  run through ctx["guard"] when wired.
- Writes runs/<date>/post-<slug>-<platform>.md. Never publishes.
"""
import os

from agents._bcommon import (
    bro_text, business_dict, ctx_guard_check, has_ai_label, make_result,
    run_dir, slug_of, state_get, state_log, state_set, write_text, PHONE,
)

ROTATION = ("before-after", "how-it-works", "walkthrough", "photo-story")

# (hook, body) per business family and format. Short, direct, no em dashes.
_TEMPLATES = {
    "gutter-genie": {
        "before-after": (
            "This gutter was full. Now it flows.",
            "Before and after from a job this week. Cleaning, downspout flush, "
            "and a free look while we are up there. Weekday visits at 12:30.",
        ),
        "how-it-works": (
            "Here is how a gutter clean works.",
            "We show up, clear the gutters and downspouts, flush them through, "
            "and show you the before and after. Free inspection with every visit.",
        ),
        "walkthrough": (
            "Walk with us through a gutter clean.",
            "Roofline to downspout, step by step. No mess left behind. "
            "Book a weekday 12:30 visit and we take care of the rest.",
        ),
        "photo-story": (
            "One house, one afternoon, clear gutters.",
            "Photos from the job. Full gutters cause real damage when it rains. "
            "Get yours checked before the next storm.",
        ),
    },
    "winnipeg-web-works": {
        "before-after": (
            "This business looked invisible online. Now it does not.",
            "Before and after from a credibility audit. Same business, same work, "
            "a web presence that finally matches it.",
        ),
        "how-it-works": (
            "Here is how a credibility audit works.",
            "We check your site, your Google presence, your reviews, and your "
            "socials, then tell you exactly what to fix first.",
        ),
        "walkthrough": (
            "Walk through a real audit with us.",
            "Site, Google, reviews, socials. Four checks, plain findings, "
            "no jargon. The audit is free.",
        ),
        "photo-story": (
            "One audit changed how this business shows up.",
            "The work was always good. The online picture did not show it. "
            "A free audit finds the gaps.",
        ),
    },
    "bnb-media": {
        "before-after": (
            "Same listing photos. A video that actually sells the stay.",
            "Before: still photos. After: a walkthrough video made from your "
            "own listing photos. Guests see the space before they book.",
        ),
        "how-it-works": (
            "Here is how a Bnb video works.",
            "Send your listing photos. We turn them into a walkthrough video "
            "guests can watch in under a minute. $199 or $299.",
        ),
        "walkthrough": (
            "Take the tour before you book.",
            "A walkthrough video shows the whole place, room by room, from "
            "your listing photos. $199 or $299.",
        ),
        "photo-story": (
            "Your photos already tell the story. We make it move.",
            "A walkthrough video from the photos you already have. More bookings "
            "start with guests seeing the space.",
        ),
    },
}

_GENERIC = {
    "before-after": (
        "Before and after from this week.",
        "Real work, real results. See the difference for yourself.",
    ),
    "how-it-works": (
        "Here is how it works.",
        "Simple process, no hassle. Get in touch and we take it from there.",
    ),
    "walkthrough": (
        "Take a look inside.",
        "A quick walkthrough of what we do and how it helps.",
    ),
    "photo-story": (
        "A job worth showing.",
        "Photos from recent work. The results speak for themselves.",
    ),
}


def _next_format(last, banned):
    """Next format in rotation after `last`, skipping banned formats."""
    banned = set(banned or [])
    order = list(ROTATION)
    if last in order:
        start = order.index(last) + 1
    else:
        start = 0
    for i in range(len(order)):
        candidate = order[(start + i) % len(order)]
        if candidate not in banned:
            return candidate
    return order[start % len(order)]


def _pick_format(requested, last, banned):
    banned = set(banned or [])
    if requested and requested in ROTATION and requested != last and requested not in banned:
        return requested
    return _next_format(last, banned)


def _template_copy(slug, fmt):
    family = _TEMPLATES.get(slug, _GENERIC)
    return family.get(fmt) or _GENERIC[fmt]


def _contact_line(slug, platform):
    if slug == "bnb-media":
        return "Message us to book." if platform == "facebook" else "DM this account to book."
    if platform == "instagram":
        return "DM this account to book."
    return "Call or text %s." % PHONE


def _clean(text):
    return (text or "").replace("\u2014", "-").replace("\u2013", "-").strip()


def run(ctx, task):
    task = dict(task or {})
    slug = slug_of(ctx)
    business = business_dict(ctx)
    log = []
    directory = run_dir(ctx, task)

    platform = (task.get("platform") or "facebook").strip().lower()
    if platform not in ("facebook", "instagram"):
        platform = "facebook"
    last = state_get(ctx, "last_format_%s" % slug)
    banned = state_get(ctx, "banned_formats", []) or []
    fmt = _pick_format((task.get("format") or "").strip().lower(), last, banned)
    if task.get("format") and task["format"] != fmt:
        log.append("format %s repeats last format %s; rotated to %s"
                   % (task.get("format"), last, fmt))

    hook, body = _template_copy(slug, fmt)
    bro_copy = bro_text(ctx, "Write a short social post for %s in the %s format. "
                        "Hook first, short plain sentences, no em dashes, no AI claims."
                        % (business.get("name", slug), fmt))
    if bro_copy:
        cleaned = _clean(bro_copy)
        if cleaned and not has_ai_label(cleaned):
            hook = cleaned.split("\n")[0].strip()
            rest = "\n".join(cleaned.split("\n")[1:]).strip()
            body = rest or body
            log.append("copy drafted with Bro, template as fallback")
        else:
            log.append("Bro copy failed checks; using template copy")
    contact = _contact_line(slug, platform)
    copy_lines = [hook, "", body, "", contact]
    copy = "\n".join(copy_lines) + "\n"
    copy = _clean(copy)

    # Self-checks: no AI label, phone rule, no em dashes.
    problems = []
    bad = has_ai_label(copy)
    if bad:
        problems.append("AI label phrase present: %s" % ", ".join(bad))
    if "\u2014" in copy or "\u2013" in copy:
        problems.append("em dash in copy")
    if slug != "bnb-media" and platform != "instagram" and PHONE not in copy:
        problems.append("phone %s missing from copy" % PHONE)
    if platform == "instagram" and PHONE in copy:
        problems.append("phone number must not appear on Instagram copy")
    if slug == "bnb-media" and PHONE in copy:
        problems.append("phone number must not appear in Bnb Media copy")

    draft = {
        "to": "",
        "from": business.get("email") or business.get("inbox") or "",
        "subject": "",
        "body": copy,
    }
    allowed, guard_reasons = ctx_guard_check(ctx, draft)
    if not allowed:
        problems.extend("guard: %s" % r for r in guard_reasons)

    path = os.path.join(directory, "post-%s-%s.md" % (slug, platform))
    text = ("PLATFORM: %s\nFORMAT: %s\nBUSINESS: %s\n\n%s"
            % (platform, fmt, slug, copy))
    write_text(path, text)
    state_set(ctx, "last_format_%s" % slug, fmt)
    state_log(ctx, "social_creator: %s %s draft written (%s)" % (slug, platform, fmt))
    log.append("wrote %s" % path)

    if problems:
        return make_result("social_creator", "held", [path],
                           "Post draft written but held: %s" % "; ".join(problems),
                           problems, log)
    return make_result("social_creator", "ok", [path],
                       "Post draft written for %s on %s (%s format)." % (slug, platform, fmt),
                       [], log)
