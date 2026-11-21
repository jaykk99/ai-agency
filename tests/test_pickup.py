"""Tests for inbox/pickup.py (Agents dashboard pickup loop).

Stdlib unittest only. Tests run against the real repo root (the
supervisor writes runs/ + state/, both gitignored) with the loop's
inbox + log redirected at a temp dir per test. Dry-run only: nothing
is ever sent, booked, or charged.

Run from the repo root:
    python3 -m unittest discover -s tests -v
"""
import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_pickup(root):
    """Import the inbox/pickup.py from the given repo copy."""
    path = os.path.join(root, "inbox", "pickup.py")
    spec = importlib.util.spec_from_file_location("pickup_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pickup_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


class PickupTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pickup = _load_pickup(REPO_ROOT)
        self._orig_inbox = self.pickup.INBOX
        self._orig_log = self.pickup.RUNS_LOG
        # Redirect the loop's inbox + log at temp dirs; the supervisor
        # still roots at the real repo (real registry + modules).
        self.inbox = os.path.join(self.tmp.name, "inbox")
        os.makedirs(self.inbox)
        self.pickup.INBOX = self.pickup.Path(self.inbox)
        self.pickup.RUNS_LOG = self.pickup.Path(
            os.path.join(self.tmp.name, "pickup.log"))
        self.supervisor = self.pickup.Supervisor(root=REPO_ROOT)

    def tearDown(self):
        self.pickup.INBOX = self._orig_inbox
        self.pickup.RUNS_LOG = self._orig_log
        self.tmp.cleanup()
        sys.modules.pop("pickup_under_test", None)

    # -- helpers ------------------------------------------------------
    def _queue(self, task_id, task):
        path = os.path.join(self.inbox, "%s.json" % task_id)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(task, fh)
        return self.pickup.Path(path)

    def _read_json(self, path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    # -- auto-agent resolution (pure, no I/O) -------------------------
    def test_resolve_lead_researcher(self):
        got = self.pickup.resolve_auto_agent(
            self.supervisor, "gutter-genie", "Find 10 new prospects in Steinbach")
        self.assertEqual(got, "gg-lead-researcher")

    def test_resolve_outreach_sender(self):
        got = self.pickup.resolve_auto_agent(
            self.supervisor, "bnb-media", "Send a first-contact email pitch")
        self.assertEqual(got, "bnb-outreach-sender")

    def test_resolve_followup_chaser(self):
        got = self.pickup.resolve_auto_agent(
            self.supervisor, "gutter-genie", "Chase the unanswered threads")
        self.assertEqual(got, "gg-followup-chaser")

    def test_resolve_quote_builder(self):
        got = self.pickup.resolve_auto_agent(
            self.supervisor, "gutter-genie", "What would the price be for 120 ft?")
        self.assertEqual(got, "gg-quote-builder")

    def test_resolve_audit_builder(self):
        got = self.pickup.resolve_auto_agent(
            self.supervisor, "winnipeg-web-works", "Run an audit for Acme Homes")
        self.assertEqual(got, "www-audit-builder")

    def test_resolve_scheduler(self):
        got = self.pickup.resolve_auto_agent(
            self.supervisor, "gutter-genie", "Schedule a visit for Tuesday")
        self.assertEqual(got, "gg-scheduler")

    def test_resolve_social_creator(self):
        got = self.pickup.resolve_auto_agent(
            self.supervisor, "gutter-genie", "Draft a Facebook post for today")
        self.assertEqual(got, "gg-social-creator")

    def test_resolve_video_producer(self):
        # New 2026-11-20 agent: bnb-video-producer must resolve, not fall back.
        got = self.pickup.resolve_auto_agent(
            self.supervisor, "bnb-media", "Make a walkthrough video for this listing")
        self.assertEqual(got, "bnb-video-producer")

    def test_resolve_reporting_shared(self):
        got = self.pickup.resolve_auto_agent(
            self.supervisor, "gutter-genie", "Build today's summary report")
        self.assertEqual(got, "reporting")

    def test_resolve_review_requester(self):
        got = self.pickup.resolve_auto_agent(
            self.supervisor, "gutter-genie", "Ask the happy customer for a review")
        self.assertEqual(got, "gg-review-requester")

    def test_resolve_customer_support(self):
        got = self.pickup.resolve_auto_agent(
            self.supervisor, "gutter-genie", "Answer this customer question")
        self.assertEqual(got, "gg-customer-support")

    def test_resolve_facebook_post_is_not_scheduler(self):
        # Regression (2026-11-21): "book" is a substring of "Facebook",
        # so plain substring matching sent every Facebook post task to
        # the scheduler. Word-boundary matching must route it to social.
        got = self.pickup.resolve_auto_agent(
            self.supervisor, "gutter-genie", "Draft a Facebook post for today")
        self.assertEqual(got, "gg-social-creator")

    def test_resolve_book_as_whole_word_still_schedules(self):
        got = self.pickup.resolve_auto_agent(
            self.supervisor, "gutter-genie", "Book a visit for Tuesday")
        self.assertEqual(got, "gg-scheduler")

    def test_resolve_unknown_falls_back_to_lead_researcher(self):
        got = self.pickup.resolve_auto_agent(
            self.supervisor, "demoboss", "do something vague with no keywords")
        self.assertEqual(got, "db-lead-researcher")

    def test_resolve_unknown_business_falls_back_to_ops_manager(self):
        got = self.pickup.resolve_auto_agent(
            self.supervisor, "no-such-business", "do something vague")
        self.assertEqual(got, "ops-manager")

    # -- queue file shape ----------------------------------------------
    def test_is_queue_file(self):
        inbox = self.pickup.INBOX
        self.assertTrue(self.pickup._is_queue_file(inbox / "task-1.json"))
        self.assertFalse(self.pickup._is_queue_file(inbox / "task-1.done.json"))
        self.assertFalse(self.pickup._is_queue_file(inbox / "task-1.failed.json"))
        self.assertFalse(self.pickup._is_queue_file(inbox / "task-1.running.json"))
        self.assertFalse(self.pickup._is_queue_file(inbox / "notes.md"))
        self.assertFalse(self.pickup._is_queue_file(inbox / "task-1.JSON"))

    # -- process_one ----------------------------------------------------
    def test_process_one_reporting_task_goes_done(self):
        path = self._queue("task-20261121-001", {
            "id": "task-20261121-001", "agent": "reporting",
            "business": "gutter-genie",
            "task": {"instruction": "Build today's report",
                     "date": "2026-11-21"},
            "source": "test", "status": "queued",
        })
        task_id, outcome = self.pickup.process_one(self.supervisor, path)
        self.assertEqual((task_id, outcome), ("task-20261121-001", "done"))
        done = os.path.join(self.inbox, "task-20261121-001.done.json")
        self.assertTrue(os.path.exists(done))
        self.assertFalse(os.path.exists(os.path.join(
            self.inbox, "task-20261121-001.json")))
        record = self._read_json(done)
        self.assertEqual(record["status"], "done")
        self.assertIn("result", record)
        self.assertIn(record["result"]["status"], ("ok", "held", "planned"))
        self.assertIn("summary", record["result"])

    def test_process_one_auto_resolves_and_runs(self):
        path = self._queue("task-20261121-002", {
            "id": "task-20261121-002", "agent": "auto",
            "business": "gutter-genie",
            "task": {"instruction": "Build today's summary report",
                     "date": "2026-11-21"},
            "status": "sent",
        })
        _, outcome = self.pickup.process_one(self.supervisor, path)
        self.assertEqual(outcome, "done")
        record = self._read_json(os.path.join(
            self.inbox, "task-20261121-002.done.json"))
        self.assertEqual(record.get("resolved_agent"), "reporting")

    def test_process_one_unknown_agent_goes_failed(self):
        path = self._queue("task-20261121-003", {
            "id": "task-20261121-003", "agent": "no-such-agent",
            "business": "gutter-genie",
            "task": {"instruction": "hello"}, "status": "queued",
        })
        _, outcome = self.pickup.process_one(self.supervisor, path)
        self.assertEqual(outcome, "failed")
        failed = os.path.join(self.inbox, "task-20261121-003.failed.json")
        self.assertTrue(os.path.exists(failed))
        record = self._read_json(failed)
        self.assertEqual(record["result"]["status"], "error")
        self.assertIn("unknown agent", record["result"]["summary"])

    def test_process_one_skips_already_done_status(self):
        path = self._queue("task-20261121-004", {
            "id": "task-20261121-004", "agent": "reporting",
            "business": "gutter-genie",
            "task": {"instruction": "Build today's report"},
            "status": "done",
        })
        _, outcome = self.pickup.process_one(self.supervisor, path)
        self.assertEqual(outcome, "skip")
        # File is un-claimed: still a plain queue file, no .done rename.
        self.assertTrue(os.path.exists(os.path.join(
            self.inbox, "task-20261121-004.json")))
        self.assertFalse(os.path.exists(os.path.join(
            self.inbox, "task-20261121-004.done.json")))

    def test_process_one_malformed_json_goes_failed(self):
        path = os.path.join(self.inbox, "task-20261121-005.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        _, outcome = self.pickup.process_one(
            self.supervisor, self.pickup.Path(path))
        self.assertEqual(outcome, "failed")
        self.assertTrue(os.path.exists(os.path.join(
            self.inbox, "task-20261121-005.failed.json")))

    def test_process_one_non_object_json_goes_failed(self):
        path = self._queue("task-20261121-006", ["not", "an", "object"])
        _, outcome = self.pickup.process_one(self.supervisor, path)
        self.assertEqual(outcome, "failed")
        self.assertTrue(os.path.exists(os.path.join(
            self.inbox, "task-20261121-006.failed.json")))

    def test_process_one_missing_agent_defaults_to_auto(self):
        path = self._queue("task-20261121-007", {
            "id": "task-20261121-007",
            "business": "gutter-genie",
            "task": {"instruction": "Build today's summary report",
                     "date": "2026-11-21"},
            "status": "queued",
        })
        _, outcome = self.pickup.process_one(self.supervisor, path)
        self.assertEqual(outcome, "done")
        record = self._read_json(os.path.join(
            self.inbox, "task-20261121-007.done.json"))
        self.assertEqual(record.get("resolved_agent"), "reporting")

    def test_process_one_finance_task_with_data(self):
        path = self._queue("task-20261121-008", {
            "id": "task-20261121-008", "agent": "finance-controller",
            "business": "shared",
            "task": {"date": "2026-11-21",
                     "invoices": [
                         {"id": "INV-1", "business": "gutter-genie",
                          "amount": 370, "status": "paid"},
                         {"id": "INV-2", "business": "gutter-genie",
                          "amount": 200, "status": "sent"}],
                     "expenses": [
                         {"amount": 50, "category": "fuel",
                          "business": "gutter-genie"}]},
            "status": "queued",
        })
        _, outcome = self.pickup.process_one(self.supervisor, path)
        self.assertEqual(outcome, "done")
        record = self._read_json(os.path.join(
            self.inbox, "task-20261121-008.done.json"))
        self.assertIn("revenue $370.00", record["result"]["summary"])

    # -- stale reclaim ---------------------------------------------------
    def test_reclaim_stale_running_file(self):
        stale = os.path.join(self.inbox, "task-old.running.json")
        with open(stale, "w", encoding="utf-8") as fh:
            json.dump({"id": "task-old", "status": "queued"}, fh)
        old = time.time() - 3600  # 1 hour old, over the 30-min threshold
        os.utime(stale, (old, old))
        fresh = os.path.join(self.inbox, "task-new.running.json")
        with open(fresh, "w", encoding="utf-8") as fh:
            json.dump({"id": "task-new", "status": "queued"}, fh)
        reclaimed = self.pickup.reclaim_stale()
        self.assertEqual(reclaimed, 1)
        self.assertTrue(os.path.exists(os.path.join(self.inbox, "task-old.json")))
        self.assertTrue(os.path.exists(fresh))

    # -- sweep -------------------------------------------------------------
    def test_sweep_processes_all_queued(self):
        self._queue("task-a", {
            "id": "task-a", "agent": "reporting", "business": "gutter-genie",
            "task": {"instruction": "report", "date": "2026-11-21"},
            "status": "queued"})
        self._queue("task-b", {
            "id": "task-b", "agent": "ops-manager", "business": "shared",
            "task": {"date": "2026-11-21"}, "status": "sent"})
        self._queue("task-c", {
            "id": "task-c", "agent": "no-such-agent", "business": "shared",
            "task": {}, "status": "queued"})
        done, failed, skipped = self.pickup.sweep()
        self.assertEqual((done, failed, skipped), (2, 1, 0))
        self.assertTrue(os.path.exists(os.path.join(self.inbox, "task-a.done.json")))
        self.assertTrue(os.path.exists(os.path.join(self.inbox, "task-b.done.json")))
        self.assertTrue(os.path.exists(os.path.join(self.inbox, "task-c.failed.json")))

    def test_sweep_empty_inbox(self):
        self.assertEqual(self.pickup.sweep(), (0, 0, 0))

    def test_sweep_ignores_done_files(self):
        with open(os.path.join(self.inbox, "task-old.done.json"),
                  "w", encoding="utf-8") as fh:
            json.dump({"id": "task-old", "status": "done"}, fh)
        self.assertEqual(self.pickup.sweep(), (0, 0, 0))

    # -- CLI ---------------------------------------------------------------
    def test_main_once_returns_zero(self):
        self._queue("task-cli", {
            "id": "task-cli", "agent": "reporting", "business": "gutter-genie",
            "task": {"instruction": "report", "date": "2026-11-21"},
            "status": "queued"})
        # main() reads sys.argv when argv is None; pass explicitly.
        self.assertEqual(self.pickup.main(["--once"]), 0)
        self.assertTrue(os.path.exists(os.path.join(
            self.inbox, "task-cli.done.json")))

    def test_main_bad_args_returns_two(self):
        self.assertEqual(self.pickup.main(["--bogus"]), 2)


if __name__ == "__main__":
    unittest.main()
