"""Read-only Gmail access for the AI agency (W3, set A).

Shells out to /opt/hatch/bin/hatch_gws_cli. Read-only operations only
(+triage search, +read). Never sends. Any failure returns [] / None.
Pure stdlib.
"""

import json
import subprocess

CLI = "/opt/hatch/bin/hatch_gws_cli"


def _run(args, timeout=60):
    try:
        proc = subprocess.run(args, capture_output=True, text=True,
                              timeout=timeout)
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _parse_messages(raw):
    """Parse CLI JSON output into a list of message dicts."""
    if not raw:
        return []
    text = raw.strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Possibly one JSON object per line.
        items = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return [_norm(m) for m in items if isinstance(m, dict)]
    if isinstance(data, dict):
        # Common shapes: {"messages": [...]}, {"results": [...]}, single msg.
        for key in ("messages", "results", "items", "threads"):
            if isinstance(data.get(key), list):
                return [_norm(m) for m in data[key] if isinstance(m, dict)]
        return [_norm(data)]
    if isinstance(data, list):
        return [_norm(m) for m in data if isinstance(m, dict)]
    return []


def _norm(m):
    frm = m.get("from") or m.get("sender") or ""
    if isinstance(frm, dict):
        frm = frm.get("email") or ""
    to = m.get("to")
    to_emails = []
    if isinstance(to, list):
        for t in to:
            if isinstance(t, dict) and t.get("email"):
                to_emails.append(t["email"])
            elif isinstance(t, str):
                to_emails.append(t)
    elif isinstance(to, str):
        to_emails = [to]
    return {
        "id": m.get("id") or m.get("message_id") or m.get("messageId"),
        "thread_id": m.get("threadId") or m.get("thread_id"),
        "subject": m.get("subject") or "",
        "from": frm,
        "to": to_emails,
        "date": m.get("date") or m.get("message_sent_at") or "",
        "snippet": m.get("snippet") or m.get("body_text") or "",
    }


def search_messages(account_id, query, max_results=10):
    """Search one Gmail account (read-only). Returns list of message dicts."""
    if not account_id or not query:
        return []
    raw = _run([CLI, "gmail", "--account", str(account_id), "+triage",
                "--query", str(query), "--max", str(int(max_results)),
                "--format", "json"])
    return _parse_messages(raw)


def read_message(account_id, msg_id):
    """Read one message (read-only). Returns dict or None."""
    if not account_id or not msg_id:
        return None
    raw = _run([CLI, "gmail", "--account", str(account_id), "+read",
                "--id", str(msg_id), "--format", "json"])
    if not raw:
        return None
    try:
        data = json.loads(raw.strip())
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict):
        return _norm(data)
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return _norm(data[0])
    return None
