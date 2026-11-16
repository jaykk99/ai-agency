"""JSON state store for the 50-agent AI agency. Stdlib only, no I/O beyond
the state directory.

Layout (under ``root``):
    state/<slug>.json       per-business key/value state (atomic writes)
    state/<slug>-log.jsonl  per-business append-only event log

``root`` is the repo root; if ``root`` itself is already a directory named
``state``, it is used directly (lets tests point State at a temp state dir).
"""

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def _slug_of(business):
    if business is None:
        return "shared"
    if isinstance(business, dict):
        return str(business.get("slug") or "shared")
    return str(business)


def _safe_slug(slug):
    # Keep filenames safe: only word chars / dash, everything else -> "_".
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(slug)).strip("._")
    return cleaned or "shared"


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


class State:
    """Per-business JSON state store rooted at ``root``."""

    def __init__(self, root):
        root = Path(root)
        if root.name == "state":
            self.state_dir = root
            self.root = root.parent
        else:
            self.root = root
            self.state_dir = root / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _state_file(self, business):
        return self.state_dir / ("%s.json" % _safe_slug(_slug_of(business)))

    def _log_file(self, business):
        return self.state_dir / ("%s-log.jsonl" % _safe_slug(_slug_of(business)))

    def _read_all(self, business):
        path = self._state_file(business)
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, business, key, default=None):
        """Return stored value for ``key`` under ``business``, or ``default``."""
        return self._read_all(business).get(key, default)

    def set(self, business, key, value):
        """Store ``value`` under ``key`` for ``business`` (atomic write)."""
        data = self._read_all(business)
        data[key] = value
        path = self._state_file(business)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
                fh.write("\n")
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return value

    def append_log(self, business, event):
        """Append ``event`` (dict) as one JSON line to the business log."""
        if not isinstance(event, dict):
            raise TypeError("event must be a dict")
        record = dict(event)
        record.setdefault("ts", _now_iso())
        record.setdefault("business", _slug_of(business))
        path = self._log_file(business)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
        return record
