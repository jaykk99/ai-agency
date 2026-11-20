"""Bnb video producer agent (bnb-media, video production).

run(ctx, task) -> dict per the agent interface.
Turns a listing's own photos into a walkthrough-style video plan at
the $199/$299 tier.

task carries:
  {"listing": {title, url, tier: "basic"|"premium", photos: [{url, caption?}]}}
  or {"listings": [...]} for a batch.

Rules from the registry and standing orders:
  - Airbnb listings only; a listing flagged as a wedding/event
    venue is refused, never produced.
  - At least 5 usable photos (a photo counts when it has an http
    URL). Fewer than 5 escalates to Jay: too few photos to make a
    video.
  - Tier pricing: basic $199, premium $299. Unknown tier defaults
    to basic.
  - Bnb copy carries no phone number; the outro call-to-action is
    "DM this account".

The agent produces a complete production package (shot list,
captions, timing, tier, price) ready for the video tool. It does
not render video itself: in dry-run the package is the deliverable
and the summary says so plainly. Writes
runs/<date>/video-plan-<slug>.json.
"""
from agents._bcommon import (
    make_result, run_date, run_dir, slug_of, state_log, write_json,
)

TIER_PRICES = {"basic": 199.0, "premium": 299.0}
MIN_PHOTOS = 5
SECONDS_PER_SHOT_BASIC = 4
SECONDS_PER_SHOT_PREMIUM = 5
INTRO_SECONDS = 3
OUTRO_SECONDS = 4
VENUE_WORDS = ("wedding", "event venue", "banquet", "conference venue",
               "reception hall")


def _usable_photos(photos):
    usable = []
    for photo in photos or []:
        if not isinstance(photo, dict):
            continue
        url = str(photo.get("url") or "").strip()
        if url.startswith("http"):
            usable.append({
                "url": url,
                "caption": str(photo.get("caption") or "").strip(),
            })
    return usable


def _is_venue(listing):
    text = ((listing.get("title") or "") + " "
            + (listing.get("description") or "") + " "
            + (listing.get("type") or "")).lower()
    return any(word in text for word in VENUE_WORDS)


def build_plan(listing):
    """Return (plan, held_reasons). plan is None when refused."""
    title = str(listing.get("title") or "Untitled listing").strip()
    if _is_venue(listing):
        return None, ["listing %r looks like a wedding/event venue; "
                      "Bnb Media is Airbnb listings only" % title]
    tier = str(listing.get("tier") or "basic").strip().lower()
    if tier not in TIER_PRICES:
        tier = "basic"
    photos = _usable_photos(listing.get("photos"))
    if len(photos) < MIN_PHOTOS:
        return None, ["listing %r has only %d usable photo(s); need at "
                      "least %d to make a video, escalate to Jay"
                      % (title, len(photos), MIN_PHOTOS)]
    seconds_per_shot = (SECONDS_PER_SHOT_PREMIUM if tier == "premium"
                        else SECONDS_PER_SHOT_BASIC)
    shots = [{
        "shot": 1,
        "kind": "intro",
        "seconds": INTRO_SECONDS,
        "caption": title,
        "url": None,
    }]
    for i, photo in enumerate(photos, 2):
        shots.append({
            "shot": i,
            "kind": "photo",
            "seconds": seconds_per_shot,
            "caption": photo["caption"] or "Room %d" % (i - 1),
            "url": photo["url"],
        })
    shots.append({
        "shot": len(shots) + 1,
        "kind": "outro",
        "seconds": OUTRO_SECONDS,
        "caption": "DM this account to book your video",
        "url": None,
    })
    duration = sum(shot["seconds"] for shot in shots)
    plan = {
        "title": title,
        "url": str(listing.get("url") or "").strip(),
        "tier": tier,
        "price": TIER_PRICES[tier],
        "currency": "CAD",
        "photos_used": len(photos),
        "shots": shots,
        "duration_seconds": duration,
        "rendering": "not rendered in dry-run; production package "
                     "ready for the video tool",
    }
    return plan, []


def run(ctx, task):
    task = dict(task or {})
    slug = slug_of(ctx)
    date = run_date(task)
    directory = run_dir(ctx, task)
    log = []

    listings = task.get("listings")
    if listings is None:
        if task.get("listing") is not None:
            listings = [task["listing"]]
        else:
            listings = []
    if isinstance(listings, dict):
        listings = [listings]
    if not isinstance(listings, list) or not listings:
        return make_result(
            "bnb_video_producer", "held", [],
            "No listing provided for %s: give listing (or listings) "
            "with photos in the task." % slug,
            ["no listing provided"], log)

    plans = []
    held = []
    for listing in listings:
        if not isinstance(listing, dict):
            held.append("a listing is not an object")
            continue
        plan, reasons = build_plan(listing)
        if plan is None:
            held.extend(reasons)
        else:
            plans.append(plan)

    report = {
        "business": slug,
        "date": date,
        "plans": plans,
        "held_reasons": held,
    }
    path = write_json(
        "%s/video-plan-%s.json" % (directory, slug), report)
    state_log(ctx, "bnb_video_producer: %s %d plan(s), %d held"
              % (slug, len(plans), len(held)))
    log.append("wrote %s" % path)
    summary = ("Bnb video plans for %s: %d production package(s) ready "
               "(rendering not done in dry-run), %d listing(s) held."
               % (slug, len(plans), len(held)))
    status = "held" if held else "ok"
    return make_result("bnb_video_producer", status, [path], summary,
                       held, log)
