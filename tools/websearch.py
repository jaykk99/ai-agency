"""Web search helper for the AI agency (W3, set A).

Pure stdlib. DuckDuckGo Lite HTML, no API key. Any failure returns [].
"""

import re
import urllib.parse
import urllib.request

_DDG_LITE = "https://lite.duckduckgo.com/lite/?q="
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36",
       "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
       "Accept-Language": "en-US,en;q=0.9",
       "Referer": "https://duckduckgo.com/"}

# Actual DDG Lite markup (single quotes, href first):
# <a rel="nofollow" href="//duckduckgo.com/l/?uddg=<urlencoded>&amp;rut=..."
#    class='result-link'>Name</a> ... <td class='result-snippet'>Snippet</td>
_LINK_RE = re.compile(
    r'href="([^"]+)"[^>]*class=[\'"]result-link[\'"][^>]*>(.*?)</a>', re.S)
_SNIPPET_RE = re.compile(
    r'class=[\'"]result-snippet[\'"][^>]*>(.*?)</td>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean(text):
    text = _TAG_RE.sub(" ", text or "")
    text = (text.replace("&amp;", "&").replace("&quot;", '"')
            .replace("&#x27;", "'").replace("&lt;", "<").replace("&gt;", ">"))
    return _WS_RE.sub(" ", text).strip()


def search(query, max_results=10):
    """Return list of {name, url, snippet} for query; [] on any failure."""
    if not query or not str(query).strip():
        return []
    url = _DDG_LITE + urllib.parse.quote_plus(str(query))
    req = urllib.request.Request(url, headers=_UA)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", "replace")
    except Exception:
        return []
    links = _LINK_RE.findall(html)
    snippets = _SNIPPET_RE.findall(html)
    out = []
    seen = set()
    for i, (href, name) in enumerate(links):
        if len(out) >= max_results:
            break
        href = href.strip().replace("&amp;", "&")
        # Unwrap DDG redirect: //duckduckgo.com/l/?uddg=<urlencoded target>
        if "uddg=" in href:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            if qs.get("uddg"):
                href = qs["uddg"][0]
        if href.startswith("//"):
            href = "https:" + href
        if not href.startswith("http") or href in seen:
            continue
        seen.add(href)
        snippet = _clean(snippets[i]) if i < len(snippets) else ""
        out.append({"name": _clean(name) or href,
                    "url": href,
                    "snippet": snippet})
    return out
