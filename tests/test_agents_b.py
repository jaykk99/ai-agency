"""Tests for set-B agents (quote builder, scheduler, social creator,
reporting, compliance guard) and tools.quotes. Stdlib unittest only.

Run from the repo root:
    python3 -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents import compliance_guard, quote_builder, reporting, scheduler, social_creator  # noqa: E402
from tools.quotes import format_quote, gutter_quote  # noqa: E402


class FakeState:
    """Minimal state store with the plain get/set/append_log shape."""

    def __init__(self):
        self.store = {}
        self.logs = []

    def get(self, key, default=None):
        return self.store.get(key, default)

    def set(self, key, value):
        self.store[key] = value

    def append_log(self, message):
        self.logs.append(message)


def make_ctx(business_slug, out_dir, state=None, bro=None, guard=None):
    return {
        "business": {"slug": business_slug, "name": business_slug},
        "dry_run": True,
        "state": state if state is not None else FakeState(),
        "bro": bro if bro is not None else (lambda prompt: None),
        "guard": guard if guard is not None else (lambda draft: True),
        "out_dir": out_dir,
    }


class TestGutterQuote(unittest.TestCase):
    def test_basic_math(self):
        q = gutter_quote(100, downspouts=4, storeys=2)
        self.assertEqual(q["total"], 335)
        labels = {item["label"]: item["amount"] for item in q["line_items"]}
        self.assertEqual(labels["Gutter cleaning (100 ft @ $1.75/ft)"], 175)
        self.assertEqual(labels["Base fee"], 100)
        self.assertEqual(labels["Downspout flush (4 @ $15.00 each)"], 60)
        self.assertEqual(q["deposit"], 100.50)

    def test_three_storey_rate(self):
        q = gutter_quote(100, storeys=3)
        self.assertEqual(q["cleaning_rate"], 2.63)
        cleaning = [i for i in q["line_items"] if "cleaning" in i["label"].lower()][0]
        self.assertEqual(cleaning["amount"], 263)
        self.assertEqual(q["total"], 363)

    def test_guards_and_travel(self):
        q = gutter_quote(0, guards_feet=50, travel="Steinbach")
        self.assertEqual(q["total"], 425 + 100 + 45)
        q2 = gutter_quote(10, travel="Grunthal")
        self.assertEqual(q2["total"], 17.50 + 100 + 55)

    def test_negative_inputs_rejected(self):
        with self.assertRaises(ValueError):
            gutter_quote(-5)
        with self.assertRaises(ValueError):
            gutter_quote(10, travel="Winnipeg")


class TestFormatQuote(unittest.TestCase):
    def test_gutter_genie_format(self):
        text = format_quote("gutter-genie", ["Clean gutters (100 ft)"],
                            [("Gutter cleaning", 175.0), ("Base fee", 100.0)], 275.0)
        self.assertIn("JOB SCOPE", text)
        self.assertIn("1. Clean gutters (100 ft)", text)
        self.assertIn("ESTIMATE BREAKDOWN", text)
        self.assertIn("-" * 30, text)
        self.assertIn("ESTIMATED TOTAL: $275.00", text)
        self.assertIn("PAYMENT TERMS", text)
        self.assertIn("30% deposit", text)
        self.assertIn("Please confirm", text)
        self.assertIn("No tax", text)
        self.assertNotIn("\u2014", text)

    def test_other_business_plain_format(self):
        text = format_quote("bnb-media", ["Walkthrough video"],
                            [("Video (premium)", 299.0)], 299.0)
        self.assertIn("SCOPE", text)
        self.assertIn("ESTIMATED TOTAL: $299.00", text)
        self.assertNotIn("PAYMENT TERMS", text)


class TestQuoteBuilder(unittest.TestCase):
    def test_gg_quote_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("gutter-genie", tmp)
            res = quote_builder.run(ctx, {"feet": 120, "downspouts": 4,
                                          "storeys": 2, "date": "2026-11-16"})
            self.assertEqual(res["status"], "ok")
            self.assertEqual(len(res["outputs"]), 1)
            self.assertTrue(os.path.exists(res["outputs"][0]))
            with open(res["outputs"][0]) as fh:
                text = fh.read()
            # 120*1.75=210 + 100 + 60 = 370
            self.assertIn("ESTIMATED TOTAL: $370.00", text)
            self.assertIn("JOB SCOPE", text)

    def test_gg_held_without_measurements(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("gutter-genie", tmp)
            res = quote_builder.run(ctx, {"downspouts": 4, "date": "2026-11-16"})
            self.assertEqual(res["status"], "held")
            self.assertEqual(res["outputs"], [])
            self.assertTrue(res["held"])

    def test_www_audit_outline(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("winnipeg-web-works", tmp)
            res = quote_builder.run(ctx, {"audit_for": "Acme Luxury Homes",
                                          "site": "https://example.com",
                                          "date": "2026-11-16"})
            self.assertEqual(res["status"], "ok")
            self.assertIn("audit-winnipeg-web-works.md", res["outputs"][0])
            with open(res["outputs"][0]) as fh:
                text = fh.read()
            self.assertIn("Acme Luxury Homes", text)
            self.assertIn("[finding pending]", text)
            self.assertIn("$10,000", text)
            self.assertNotIn("verified", text.lower().replace("cannot", ""))

    def test_www_held_without_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("winnipeg-web-works", tmp)
            res = quote_builder.run(ctx, {"site": "https://example.com"})
            self.assertEqual(res["status"], "held")


class TestScheduler(unittest.TestCase):
    def test_weekend_request_moves_to_monday(self):
        # 2026-11-14 is a Saturday.
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("gutter-genie", tmp)
            res = scheduler.run(ctx, {"contact": "Test Person",
                                      "requested": "2026-11-14 12:30",
                                      "date": "2026-11-16"})
            self.assertEqual(res["status"], "held")
            with open(res["outputs"][0]) as fh:
                record = json.load(fh)
            self.assertEqual(record["proposed"], "2026-11-16 12:30")
            self.assertEqual(record["status"], "held")
            self.assertIn("needs Jay confirm", record["reason"])

    def test_wrong_time_moves_to_1230(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("gutter-genie", tmp)
            res = scheduler.run(ctx, {"contact": "Test Person",
                                      "requested": "2026-11-17 10:00",
                                      "date": "2026-11-16"})
            self.assertEqual(res["status"], "held")
            with open(res["outputs"][0]) as fh:
                record = json.load(fh)
            self.assertEqual(record["proposed"], "2026-11-17 12:30")

    def test_valid_slot_proposed_as_is(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("gutter-genie", tmp)
            res = scheduler.run(ctx, {"contact": "Test Person",
                                      "requested": "2026-11-17 12:30",
                                      "date": "2026-11-16"})
            self.assertEqual(res["status"], "ok")
            with open(res["outputs"][0]) as fh:
                record = json.load(fh)
            self.assertEqual(record["proposed"], "2026-11-17 12:30")
            self.assertEqual(record["status"], "proposed")

    def test_missing_contact_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("gutter-genie", tmp)
            res = scheduler.run(ctx, {"requested": "2026-11-17 12:30"})
            self.assertEqual(res["status"], "held")
            self.assertEqual(res["outputs"], [])


class TestSocialCreator(unittest.TestCase):
    def test_never_repeats_last_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = FakeState()
            state.set("last_format_gutter-genie", "before-after")
            ctx = make_ctx("gutter-genie", tmp, state=state)
            res = social_creator.run(ctx, {"platform": "facebook",
                                           "format": "before-after",
                                           "date": "2026-11-16"})
            self.assertEqual(res["status"], "ok")
            with open(res["outputs"][0]) as fh:
                text = fh.read()
            self.assertIn("FORMAT: how-it-works", text)
            self.assertEqual(state.get("last_format_gutter-genie"), "how-it-works")

    def test_facebook_has_phone_instagram_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("gutter-genie", tmp)
            fb = social_creator.run(ctx, {"platform": "facebook",
                                          "format": "walkthrough",
                                          "date": "2026-11-16"})
            with open(fb["outputs"][0]) as fh:
                fb_text = fh.read()
            self.assertIn("204-972-0325", fb_text)
            ig = social_creator.run(ctx, {"platform": "instagram",
                                          "format": "photo-story",
                                          "date": "2026-11-16"})
            with open(ig["outputs"][0]) as fh:
                ig_text = fh.read()
            self.assertNotIn("204-972-0325", ig_text)
            self.assertIn("DM this account", ig_text)

    def test_bnb_never_has_phone(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("bnb-media", tmp)
            res = social_creator.run(ctx, {"platform": "facebook",
                                           "format": "walkthrough",
                                           "date": "2026-11-16"})
            with open(res["outputs"][0]) as fh:
                text = fh.read()
            self.assertNotIn("204-972-0325", text)
            self.assertIn("$199", text)

    def test_no_ai_label_or_em_dash(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("gutter-genie", tmp)
            res = social_creator.run(ctx, {"platform": "facebook",
                                           "date": "2026-11-16"})
            with open(res["outputs"][0]) as fh:
                text = fh.read()
            self.assertNotIn("built by AI", text)
            self.assertNotIn("\u2014", text)


class TestReporting(unittest.TestCase):
    def test_counts_from_runs_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs_path = os.path.join(tmp, "runs.jsonl")
            rows = [
                {"date": "2026-11-16", "business": "gutter-genie",
                 "agent": "lead-researcher", "type": "lead"},
                {"date": "2026-11-16", "business": "gutter-genie",
                 "agent": "lead-researcher", "type": "lead"},
                {"date": "2026-11-16", "business": "gutter-genie",
                 "agent": "quote_builder", "type": "quote"},
                {"date": "2026-11-16", "business": "gutter-genie",
                 "agent": "social_creator", "type": "post"},
                {"date": "2026-11-16", "business": "gutter-genie",
                 "agent": "outreach", "type": "draft"},
                {"date": "2026-11-16", "business": "gutter-genie",
                 "agent": "scheduler", "type": "followup_due"},
                {"date": "2026-11-16", "business": "gutter-genie",
                 "agent": "scheduler", "status": "held",
                 "held": ["needs Jay confirm"]},
                {"date": "2026-11-15", "business": "gutter-genie",
                 "agent": "lead-researcher", "type": "lead"},
            ]
            with open(runs_path, "w") as fh:
                for row in rows:
                    fh.write(json.dumps(row) + "\n")
            ctx = make_ctx("gutter-genie", tmp)
            res = reporting.run(ctx, {"date": "2026-11-16",
                                      "runs_file": runs_path})
            self.assertEqual(res["status"], "ok")
            with open(res["outputs"][0]) as fh:
                text = fh.read()
            self.assertIn("Leads found: 2", text)
            self.assertIn("Quotes built: 1", text)
            self.assertIn("Posts drafted: 1", text)
            self.assertIn("Drafts written: 1", text)
            self.assertIn("Follow-ups due: 1", text)
            self.assertIn("HELD ITEMS (1)", text)
            self.assertIn("needs Jay confirm", text)


class TestComplianceGuard(unittest.TestCase):
    def _drafts_file(self, tmp, drafts):
        path = os.path.join(tmp, "drafts.json")
        with open(path, "w") as fh:
            json.dump(drafts, fh)
        return path

    def test_good_and_bad_drafts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._drafts_file(tmp, [
                {"to": "customer@example.com", "from": "guttergeniewpg@gmail.com",
                 "subject": "Your gutter quote", "body": "Here is your quote.",
                 "business": "gutter-genie", "channel": "email"},
                {"to": "info@somecompany.com", "from": "guttergeniewpg@gmail.com",
                 "subject": "Hello", "body": "This was built by AI, check it out.",
                 "business": "gutter-genie", "channel": "email"},
            ])
            ctx = make_ctx("shared", tmp)
            res = compliance_guard.run(ctx, {"drafts_file": path,
                                             "date": "2026-11-16"})
            self.assertEqual(res["status"], "held")
            with open(res["outputs"][0]) as fh:
                report = json.load(fh)
            self.assertEqual(report["passed"], 1)
            self.assertEqual(len(report["failed"]), 1)
            reasons = " ".join(report["failed"][0]["reasons"])
            self.assertIn("info@", reasons)
            self.assertIn("built by ai", reasons)

    def test_wrong_sender_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._drafts_file(tmp, [
                {"to": "customer@example.com", "from": "winnipegwebworks@gmail.com",
                 "subject": "Quote", "body": "Hello.",
                 "business": "gutter-genie", "channel": "email"},
            ])
            ctx = make_ctx("shared", tmp)
            res = compliance_guard.run(ctx, {"drafts_file": path,
                                             "date": "2026-11-16"})
            with open(res["outputs"][0]) as fh:
                report = json.load(fh)
            self.assertEqual(report["passed"], 0)
            self.assertIn("guttergeniewpg@gmail.com",
                          " ".join(report["failed"][0]["reasons"]))

    def test_single_text_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("shared", tmp)
            res = compliance_guard.run(ctx, {
                "text": "Hi, here is your video.",
                "business": "bnb-media",
                "recipient": "host@airbnb.example.com",
                "channel": "email",
                "source_tag": "airbnb",
                "date": "2026-11-16"})
            self.assertEqual(res["status"], "ok")


class TestRealStateAdapter(unittest.TestCase):
    def test_bcommon_works_with_core_state(self):
        from core.state import State
        with tempfile.TemporaryDirectory() as tmp:
            state = State(os.path.join(tmp, "state"))
            ctx = make_ctx("gutter-genie", tmp, state=state)
            res = quote_builder.run(ctx, {"feet": 100, "downspouts": 4,
                                          "storeys": 2, "date": "2026-11-16"})
            self.assertEqual(res["status"], "ok")
            self.assertEqual(state.get("gutter-genie", "last_quote_total_gutter-genie"),
                             335)


if __name__ == "__main__":
    unittest.main()
