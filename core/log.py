"""Run logging for the 50-agent AI agency. Stdlib only.

Every supervisor run appends one JSON line to runs/runs.jsonl.
"""

import json
from datetime import datetime, timezone
from pathlib import Path


def log_run(entry, runs_file):
    """Append ``entry`` (dict) as one JSON line.

    ``runs_file`` is the runs.jsonl file path; if a directory is passed,
    runs.jsonl inside it is used. Parent directories are created.
    """
    if not isinstance(entry, dict):
        raise TypeError("entry must be a dict")
    path = Path(runs_file)
    if path.suffix != ".jsonl":
        path = path / "runs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = dict(entry)
    record.setdefault("ts", datetime.now(timezone.utc).isoformat())
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
    return record
