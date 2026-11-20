"""Lead source hunter agent (shared, lead sourcing).

run(ctx, task) -> dict per the agent interface.
Finds new places to hunt leads for all businesses: directories,
listing sites, and local sources worth mining.

task carries:
  {"business": slug, "queries": [str, ...]}  (queries optional;
  defaults are built from the business's services)
  {"skip_web": true}  (no network; used by tests and offline runs)

Search runs through ctx["search"] when provided, else
tools.websearch.search (keyless DuckDuckGo Lite). Results are
deduped by URL, Integrity Roofing sources are dropped, and any
source whose snippet mentions payment or signup is flagged: the
agent never pays for or signs up to a lead source without Jay.
Writes runs/<date>/lead-sources-<slug>.json with the source list
and quality notes. Dry-run only: research, no signups, no spend.
"""
from agents._bcommon import (
    make_result, run_date, run_dir, slug_of, state_get, state_log,
    state_set, write_json,
)

try:
    from agents._common import BUSINESSES as _KNOWN
except Exception:
    _KNOWN = {}

BLOCKED_MARKERS = ("integrityroofing", "integrity roofing")
PAID_MARKERS = ("pricing", "paid", "subscribe", "subscription",
                "sign up", "signup", "buy now", "purchase")
DIRECTORY_MARKERS = ("director", "listing", "marketplace", "yelp",
                     "yellowpages", "bbb", "houzz", "homestars",
                     "kijiji", "facebook", "association")

DEFAULT_QUERIES = {
    "gutter-genie": [
        "Winnipeg property management companies",
        "Winnipeg roofing companies directory",
        "Winnipeg home services listings",
    ],
    "winnipeg-web-works": [
        "Winnipeg luxury home builders",
        "Winnipeg high net worth business owners directory",
        "Manitoba luxury real estate brokerages",
    ],
    "move-and-metal": [
        "Winnipeg moving companies directory",
        "Winnipeg metal fabrication companies",
        "Winnipeg construction companies list",
    ],
    "bnb-media": [
        "Winnipeg Airbnb hosts directory",
        "Manitoba short term rental listings",
        "Winnipeg vacation rental owners",
    ],
    "demoboss": [
        "Winnipeg demolition companies",
        "Manitoba demolition contractors directory",
        "Winnipeg construction demolition listings",
    ],
    "quonset-place": [
        "Quonset hut buyers Canada",
        "steel building buyers Manitoba",
        "farm buildings for sale Manitoba listings",
    ],
    "drip-detailing": [
        "Winnipeg car detailing customers",
        "Winnipeg auto detailing directory",
        "Manitoba car dealerships list",
    ],
}


def _default_queries(slug):
    return list(DEFAULT_QUERIES.get(slug) or
                ["%s local business directory" % slug.replace("-", " "),
                 "%s companies Winnipeg" % slug.replace("-", " ")])


def _is_blocked(url, name):
    text = ((url or "") + " " + (name or "")).lower()
    return any(marker in text for marker in BLOCKED_MARKERS)


def _quality_notes(result):
    snippet = (result.get("snippet") or "").lower()
    notes = []
    if any(marker in snippet for marker in PAID_MARKERS):
        notes.append("may require payment or signup; escalate to Jay "
                     "before paying or signing up")
    if any(marker in (result.get("url") or "").lower()
           or marker in snippet for marker in DIRECTORY_MARKERS):
        notes.append("looks like a directory or listing source")
    if len(snippet) < 20:
        notes.append("thin snippet; verify the source by hand")
    return notes


def run(ctx, task):
    task = dict(task or {})
    slug = slug_of(ctx)
    date = run_date(task)
    directory = run_dir(ctx, task)
    log = []

    queries = task.get("queries")
    if queries is None:
        queries = _default_queries(slug)
    if isinstance(queries, str):
        queries = [queries]
    if not queries:
        return make_result(
            "lead_source_hunter", "held", [],
            "No search queries for %s." % slug,
            ["no search queries provided"], log)

    search = ctx.get("search")
    if not callable(search):
        if task.get("skip_web"):
            search = lambda query, max_results=10: []  # noqa: E731
        else:
            try:
                from tools.websearch import search as _web_search
                search = _web_search
            except Exception:
                search = lambda query, max_results=10: []  # noqa: E731

    sources = []
    seen = set()
    for query in queries:
        try:
            results = search(query, max_results=10) or []
        except Exception as exc:
            log.append("search failed for %r: %s" % (query, exc))
            continue
        for result in results:
            if not isinstance(result, dict):
                continue
            url = str(result.get("url") or "").strip()
            name = str(result.get("name") or url).strip()
            if not url or url in seen or _is_blocked(url, name):
                continue
            seen.add(url)
            sources.append({
                "name": name,
                "url": url,
                "snippet": str(result.get("snippet") or "").strip(),
                "query": query,
                "notes": _quality_notes(result),
            })

    paid_flags = [s for s in sources
                  if any("payment or signup" in n for n in s["notes"])]
    report = {
        "business": slug,
        "date": date,
        "queries": list(queries),
        "sources": sources,
        "paid_or_signup_flags": len(paid_flags),
    }
    path = write_json(
        "%s/lead-sources-%s.json" % (directory, slug), report)
    state_set(ctx, "lead_sources", sources)
    state_log(ctx, "lead_source_hunter: %s found %d sources from %d "
                   "queries" % (slug, len(sources), len(queries)))
    log.append("wrote %s" % path)
    held = []
    if not sources:
        held.append("no lead sources found for %s" % slug)
    if paid_flags:
        held.append("%d source(s) may need payment or signup; Jay "
                    "decides before any spend" % len(paid_flags))
    summary = ("Lead sources for %s: %d sources from %d queries, %d "
               "need Jay's call before payment/signup."
               % (slug, len(sources), len(queries), len(paid_flags)))
    status = "held" if held else "ok"
    return make_result("lead_source_hunter", status, [path], summary,
                       held, log)
