# AI Agency

A 50-agent AI agency that runs Jay's 7 businesses: lead gen, outreach, follow-up, quoting, scheduling, support, social, ads, invoicing, and bookkeeping. Shared ops, finance, quality, and compliance agents sit across all 7.

## The 7 businesses

| Business | Inbox | Site | Phone |
|---|---|---|---|
| Gutter Genie | guttergeniewpg@gmail.com | https://guttergenie.pages.dev | 204-972-0325 |
| Winnipeg Web Works | winnipegwebworks@gmail.com | https://winnipeg-web-works.pages.dev | 204-972-0325 |
| Move & Metal | metalmoovers@hotmail.com (Outlook, needs reauth) | https://move-and-metal-site.pages.dev | 204-972-0325 |
| Bnb Media | bnbmediawpg@gmail.com | https://bnb-media-site.pages.dev | none in copy |
| DemoBoss | demobosswpg@hotmail.com (Outlook, needs reauth) | https://demoboss-site.pages.dev | 204-972-0325 |
| Quonset Place | thequonsetplace@hotmail.com (Outlook, needs reauth) | https://quonset-place-site.pages.dev | 204-972-0325 |
| Drip Detailing | none yet — outreach held until an inbox exists | — | 204-972-0325 |

Full details (services, rate cards, visit rules, notes) live in `config/businesses.yaml`. The full 50-agent roster lives in `agents/registry.yaml` and `docs/ROSTER.md`.

## How to run (dry-run)

```bash
python3 runner.py --all --dry-run
```

Dry-run only: agents research, draft, and log, but nothing is sent, booked, or charged. Live runs need Jay's approval first.

## Standing rules (non-negotiable)

- Business email only from that business's own inbox. Mismatch = hold, never reroute.
- No info@ sends, ever.
- Bulk outreach sends with no approval step once live is approved, but money moves, bookings, and anything charging Jay need his tap.
- Never put an AI label / "built by AI" / "made by AI" in any public copy, on any surface.
- Phone 204-972-0325 on every post except Instagram (CTA: "DM this account"). Bnb copy carries no phone.
- Never contact or partner with Integrity Roofing (integrityroofinginc.ca), ex-employer.
- Bnb Media outreach only to Airbnb hosts and people who post houses on listing sites. Never wedding/event venues.
- Verify every URL live before naming it.
- Gutter Genie visits weekdays 12:30 PM only. Winnipeg Web Works is organic marketing only, no paid ads.
- Outlook inboxes (Move & Metal, DemoBoss, Quonset Place) need reauth; outreach from them is held until then. Drip Detailing has no inbox; its outreach is held until one exists.

## Layout

- `config/businesses.yaml` — the 7 businesses
- `agents/registry.yaml` — all 50 agents (37 working, 13 defined)
- `docs/ROSTER.md` — roster table
- `runner.py` — dry-run runner (supervisor's deliverable)
