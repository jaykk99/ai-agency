"""Supervisor: loads the registry + business configs, routes tasks to agents.

Registry / businesses files use a minimal YAML subset (simple ``key: value``
pairs, ``- item`` lists, nested maps/lists, inline ``[a, b]`` lists, quoted
scalars). No PyYAML required: a small tolerant parser below handles it,
falling back to PyYAML when installed.

File shapes (both accepted):
    agents/registry.yaml:   either a bare list, or a dict with top-level
                            "agents" holding the list (the real file uses
                            the wrapped form).
    config/businesses.yaml: either a bare list, or a dict with top-level
                            "businesses" holding the list (real file: wrapped).

Routing contract:
    task = {"agent": <id>, "business": <slug>, "task": {...}}
    - unknown agent / missing files -> {"status": "error", ...} (no crash)
    - agent status == "defined" or module is None -> PLAN MODE: writes
      runs/<date>/plan-<agent>.md, returns status "planned"
    - agent status == "working" -> import module, call run(ctx, task)
    - import/call errors -> status "error", exception text in log
    - every run is logged via core.log to runs/runs.jsonl
"""

import importlib
import json
import sys
from datetime import date as _date
from pathlib import Path

ROOT = Path(__file__).resolve().parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import bro as bro_mod  # noqa: E402
from core.guard import check_outbound  # noqa: E402
from core.log import log_run  # noqa: E402
from core.state import State  # noqa: E402


# ---------------------------------------------------------------- YAML ---

def _strip_comment(line):
    """Remove a trailing '# comment' outside of quotes."""
    in_single = in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            if i == 0 or line[i - 1] in (" ", "\t"):
                return line[:i].rstrip()
    return line


def _split_top(text):
    """Split on top-level commas (not inside [] or quotes)."""
    parts, depth, cur, quote = [], 0, "", None
    for ch in text:
        if quote:
            cur += ch
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            cur += ch
        elif ch == "[":
            depth += 1
            cur += ch
        elif ch == "]":
            depth -= 1
            cur += ch
        elif ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip() != "":
        parts.append(cur)
    return parts


def _parse_scalar(text):
    text = text.strip()
    if text in ("", "~", "null", "Null", "NULL"):
        return None
    low = text.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(p) for p in _split_top(inner)]
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def _tokenize(text):
    """Return [(indent, content)] for non-blank, non-comment lines."""
    lines = []
    for raw in text.splitlines():
        if "\t" in raw[:len(raw) - len(raw.lstrip())]:
            raw = raw.replace("\t", "  ")
        stripped = _strip_comment(raw).rstrip()
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        lines.append((indent, stripped.strip()))
    return lines


def _parse_block(lines, i, indent):
    """Parse one block starting at lines[i] with the given indent.

    Returns (value, next_index). Value is a list, a dict, or a scalar.
    """
    if i >= len(lines):
        return None, i
    first_indent, first = lines[i]
    if first_indent < indent:
        return None, i
    if first.startswith("- ") or first == "-":
        # List block.
        items = []
        while i < len(lines):
            ind, content = lines[i]
            if ind != first_indent or not (content.startswith("- ") or content == "-"):
                break
            after = content[1:].strip()
            if after == "":
                value, i = _parse_block(lines, i + 1, ind + 1)
                items.append(value)
                continue
            if ":" in after and not after.startswith(("[", '"', "'")):
                # "- key: value" starts an inline mapping; following keys
                # sit at indent ind + 2, and anything deeper nested under
                # them (e.g. a list under a key) must come along too.
                sub = [(ind + 2, after)]
                j = i + 1
                while j < len(lines) and lines[j][0] > ind:
                    sub.append(lines[j])
                    j += 1
                mapping, _ = _parse_block(sub, 0, ind + 2)
                items.append(mapping)
                i = j
                continue
            text = after
            j = i + 1
            while j < len(lines) and lines[j][0] > ind:
                text += " " + lines[j][1]
                j += 1
            items.append(_parse_scalar(text))
            i = j
        return items, i
    if ":" in first:
        # Mapping block.
        mapping = {}
        while i < len(lines):
            ind, content = lines[i]
            if ind != first_indent or content.startswith("- "):
                break
            if ":" not in content:
                break
            key, _, rest = content.partition(":")
            key = key.strip()
            rest = rest.strip()
            i += 1
            if rest != "":
                # Multi-line scalar: more-indented lines following a
                # scalar value are its continuation (YAML folds them to
                # spaces). This covers the folded quoted/plain scalars
                # used in the real registry.yaml.
                value_text = rest
                while i < len(lines) and lines[i][0] > ind:
                    value_text += " " + lines[i][1]
                    i += 1
                mapping[key] = _parse_scalar(value_text)
            else:
                # Nested block (map / list) or empty -> None. A list at
                # the same indent as the key is also its value (indentless
                # sequence, valid YAML).
                if i < len(lines) and (lines[i][0] > ind or (
                        lines[i][0] == ind
                        and lines[i][1].startswith("- "))):
                    value, i = _parse_block(lines, i, lines[i][0])
                    mapping[key] = value
                else:
                    mapping[key] = None
        return mapping, i
    # Bare scalar.
    return _parse_scalar(first), i + 1


def parse_simple_yaml(text):
    """Parse the minimal YAML subset used by registry/businesses files."""
    lines = _tokenize(text)
    if not lines:
        return None
    value, _ = _parse_block(lines, 0, lines[0][0])
    return value


def load_yaml(path):
    """Load a YAML file. Tries PyYAML if installed, else the local parser.

    Returns the parsed document (list, dict, or None). Raises
    FileNotFoundError when the file does not exist.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
        try:
            return yaml.safe_load(text)
        except Exception:
            pass  # fall through to the tolerant local parser
    except ImportError:
        pass
    return parse_simple_yaml(text)


def unwrap_list(doc, key):
    """Accept a bare list, or a dict wrapping the list under ``key``.

    Returns the list, or [] when the shape is unrecognised.
    """
    if isinstance(doc, list):
        return doc
    if isinstance(doc, dict):
        value = doc.get(key)
        if isinstance(value, list):
            return value
    return []


# ------------------------------------------------------------- Supervisor ---

def _today(date=None):
    if date:
        return str(date)
    return _date.today().isoformat()


class Supervisor:
    """Routes tasks to agents according to the registry."""

    def __init__(self, root=None, registry_path=None, businesses_path=None):
        self.root = Path(root) if root is not None else ROOT
        self.registry_path = Path(registry_path) if registry_path else \
            self.root / "agents" / "registry.yaml"
        self.businesses_path = Path(businesses_path) if businesses_path else \
            self.root / "config" / "businesses.yaml"
        self.state = State(self.root)
        self._registry = None
        self._businesses = None

    # -- config loading (missing files = clear error, not a traceback) --
    def load_registry(self):
        if self._registry is None:
            if not self.registry_path.exists():
                raise FileNotFoundError(
                    "registry file not found: %s" % self.registry_path)
            self._registry = unwrap_list(
                load_yaml(self.registry_path), "agents")
        return self._registry

    def load_businesses(self):
        if self._businesses is None:
            if not self.businesses_path.exists():
                raise FileNotFoundError(
                    "businesses file not found: %s" % self.businesses_path)
            self._businesses = unwrap_list(
                load_yaml(self.businesses_path), "businesses")
        return self._businesses

    def find_agent(self, agent_id):
        for entry in self.load_registry():
            if isinstance(entry, dict) and entry.get("id") == agent_id:
                return entry
        return None

    def find_business(self, slug):
        if slug is None:
            return None
        for entry in self.load_businesses():
            if isinstance(entry, dict) and entry.get("slug") == slug:
                return entry
        return None

    # -- routing --
    def run(self, task, dry_run=True, date=None):
        """Route one task dict. Always returns a result dict, never raises
        for expected failures (unknown agent, missing files, import errors).
        """
        date_str = _today(date)
        agent_id = (task or {}).get("agent")
        business_slug = (task or {}).get("business")
        inner = (task or {}).get("task") or {}
        log_lines = []
        result = {
            "status": "error",
            "agent": agent_id,
            "outputs": [],
            "summary": "",
            "held": [],
            "log": log_lines,
        }

        def finish(status, summary, outputs=None, held=None):
            result["status"] = status
            result["summary"] = summary
            result["outputs"] = list(outputs or [])
            result["held"] = list(held or [])
            result["log"] = list(log_lines)
            try:
                log_run({
                    "date": date_str,
                    "agent": agent_id,
                    "business": business_slug,
                    "status": status,
                    "summary": summary,
                    "dry_run": bool(dry_run),
                }, self.root / "runs" / "runs.jsonl")
            except Exception as exc:  # logging must never break a run
                log_lines.append("run logging failed: %s" % exc)
                result["log"] = list(log_lines)
            return result

        if not agent_id:
            return finish("error", "task is missing 'agent' id")
        try:
            agent = self.find_agent(agent_id)
        except FileNotFoundError as exc:
            return finish("error", str(exc))
        except Exception as exc:
            return finish("error", "could not load registry: %s" % exc)
        if agent is None:
            return finish("error", "unknown agent id: %s" % agent_id)

        # Business config: 'shared' agents may run without a business.
        business = None
        if business_slug and business_slug != "shared":
            try:
                business = self.find_business(business_slug)
            except FileNotFoundError as exc:
                return finish("error", str(exc))
            except Exception as exc:
                return finish("error", "could not load businesses: %s" % exc)
            if business is None:
                return finish("error", "unknown business slug: %s"
                             % business_slug)
        else:
            business = {"slug": agent.get("business") or "shared"}

        status = str(agent.get("status") or "defined").lower()
        module = agent.get("module")
        out_dir = self.root / "runs" / date_str
        out_dir.mkdir(parents=True, exist_ok=True)

        # PLAN MODE for defined-only agents.
        if status == "defined" or not module:
            plan_path = out_dir / ("plan-%s.md" % agent_id)
            plan = self._plan_text(agent, business, inner, date_str)
            try:
                plan_path.write_text(plan, encoding="utf-8")
            except OSError as exc:
                return finish("error", "could not write plan file: %s" % exc)
            log_lines.append("plan mode: wrote %s" % plan_path)
            return finish(
                "planned",
                "plan written for defined-only agent %s" % agent_id,
                outputs=[str(plan_path)])

        # WORKING MODE: import the module and call run(ctx, task).
        ctx = {
            "business": business,
            "dry_run": bool(dry_run),
            "state": self.state,
            "bro": bro_mod.call_bro,
            "guard": check_outbound,
            "out_dir": str(out_dir),
        }
        try:
            mod = importlib.import_module(module)
            run_fn = getattr(mod, "run")
            outcome = run_fn(ctx, inner)
        except Exception as exc:
            log_lines.append("agent %s failed: %s: %s"
                             % (agent_id, type(exc).__name__, exc))
            return finish("error", "agent %s failed: %s: %s"
                          % (agent_id, type(exc).__name__, exc))
        if not isinstance(outcome, dict):
            return finish("error", "agent %s returned non-dict result"
                          % agent_id)
        merged = {
            "status": outcome.get("status", "error"),
            "agent": outcome.get("agent", agent_id),
            "outputs": list(outcome.get("outputs") or []),
            "summary": outcome.get("summary", ""),
            "held": list(outcome.get("held") or []),
            "log": list(log_lines) + list(outcome.get("log") or []),
        }
        if merged["status"] not in ("ok", "held", "error", "planned"):
            merged["status"] = "error"
            merged["summary"] = ("agent %s returned unknown status %r"
                                 % (agent_id, outcome.get("status")))
        # Log the agent's own outcome via finish() path.
        result.update(merged)
        try:
            log_run({
                "date": date_str,
                "agent": agent_id,
                "business": business_slug,
                "status": merged["status"],
                "summary": merged["summary"],
                "dry_run": bool(dry_run),
            }, self.root / "runs" / "runs.jsonl")
        except Exception as exc:
            merged["log"].append("run logging failed: %s" % exc)
            result["log"] = list(merged["log"])
        return result

    @staticmethod
    def _plan_text(agent, business, inner, date_str):
        def fmt(value):
            if isinstance(value, list):
                return ", ".join(str(v) for v in value) if value else "(none)"
            if value is None:
                return "(none)"
            return str(value)

        lines = [
            "# Plan: %s (%s)" % (agent.get("name") or agent.get("id"),
                                 agent.get("id")),
            "",
            "- Date: %s" % date_str,
            "- Business: %s" % (business.get("slug") if isinstance(business, dict) else business),
            "- Role: %s" % fmt(agent.get("role")),
            "- Status: defined (plan mode only, no live work done)",
            "",
            "## Description",
            "",
            fmt(agent.get("description")),
            "",
            "## What this agent would do",
            "",
            "1. Gather inputs: %s" % fmt(agent.get("inputs")),
            "2. Use tools: %s" % fmt(agent.get("tools")),
            "3. Produce outputs: %s" % fmt(agent.get("outputs")),
            "4. Respect standing rules: own-inbox sends only, no info@, "
            "no AI label in public copy, phone on non-Instagram posts, "
            "Bnb Airbnb/listing sources only, money moves held for Jay.",
            "",
            "## Inputs needed",
            "",
            "- %s" % fmt(agent.get("inputs")),
            "",
            "## Task received",
            "",
            "```json",
            json.dumps(inner, indent=2, sort_keys=True, default=str),
            "```",
            "",
            "## Escalation",
            "",
            fmt(agent.get("escalation")),
            "",
        ]
        return "\n".join(lines)


def run_task(task, root=None, dry_run=True, date=None):
    """Convenience wrapper: route one task with a fresh Supervisor."""
    return Supervisor(root=root).run(task, dry_run=dry_run, date=date)
