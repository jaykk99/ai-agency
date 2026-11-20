"""Tests for set-C agents (implemented 2026-11-20): ops_manager,
finance_controller, quality_control, lead_source_hunter, ads_manager,
invoicing_tracker, bookkeeper, customer_support, review_requester,
bnb_video_producer. Stdlib unittest only.

Run from the repo root:
    python3 -m unittest discover -s tests -v
No network: lead_source_hunter tests inject a fake search function.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents import (  # noqa: E402
    ads_manager, bnb_video_producer, bookkeeper, customer_support,
    finance_controller, invoicing_tracker, lead_source_hunter,
    ops_manager, quality_control, review_requester,
)
from supervisor import Supervisor  # noqa: E402


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


def make_ctx(business_slug, out_dir, state=None, bro=None, guard=None,
             search=None):
    ctx = {
        "business": {"slug": business_slug, "name": business_slug},
        "dry_run": True,
        "state": state if state is not None else FakeState(),
        "bro": bro if bro is not None else (lambda prompt: None),
        "guard": guard if guard is not None else (lambda draft: True),
        "out_dir": out_dir,
    }
    if search is not None:
        ctx["search"] = search
    return ctx


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------- bookkeeper ---

class TestBookkeeper(unittest.TestCase):
    def test_balances_and_reconciles(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("gutter-genie", tmp)
            res = bookkeeper.run(ctx, {
                "entries": [
                    {"date": "2026-11-20", "type": "income",
                     "amount": 370, "description": "GG job",
                     "invoice_id": "INV-1"},
                    {"date": "2026-11-20", "type": "expense",
                     "amount": 45, "description": "Fuel"},
                ],
                "invoices": [{"id": "INV-1", "amount": 370,
                              "status": "paid"}],
                "date": "2026-11-20",
            })
            self.assertEqual(res["status"], "ok")
            data = read_json(res["outputs"][0])
            self.assertEqual(data["income_total"], 370)
            self.assertEqual(data["expense_total"], 45)
            self.assertEqual(data["balance"], 325)
            self.assertTrue(data["reconciled"])
            self.assertEqual(ctx["state"].get("ledger_balance"), 325)

    def test_paid_invoice_without_income_is_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("gutter-genie", tmp)
            res = bookkeeper.run(ctx, {
                "entries": [{"date": "2026-11-20", "type": "expense",
                             "amount": 10, "description": "Fuel"}],
                "invoices": [{"id": "INV-9", "amount": 200,
                              "status": "paid"}],
                "date": "2026-11-20",
            })
            self.assertEqual(res["status"], "held")
            data = read_json(res["outputs"][0])
            self.assertEqual(len(data["mismatches"]), 1)
            self.assertIn("INV-9", data["mismatches"][0])

    def test_bad_entries_are_problems_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("shared", tmp)
            res = bookkeeper.run(ctx, {
                "entries": [
                    {"date": "2026-11-20", "type": "income",
                     "amount": "abc", "description": "bad amount"},
                    {"date": "not-a-date", "type": "income",
                     "amount": 10, "description": "bad date"},
                    {"date": "2026-11-20", "type": "gift",
                     "amount": 10, "description": "bad type"},
                    {"date": "2026-11-20", "type": "income",
                     "amount": 50, "description": "good"},
                ],
                "date": "2026-11-20",
            })
            self.assertEqual(res["status"], "held")
            data = read_json(res["outputs"][0])
            self.assertEqual(len(data["entries"]), 1)
            self.assertEqual(len(data["problems"]), 3)

    def test_no_entries_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("shared", tmp)
            res = bookkeeper.run(ctx, {"date": "2026-11-20"})
            self.assertEqual(res["status"], "held")
            self.assertEqual(res["outputs"], [])


# ----------------------------------------------------- invoicing tracker ---

class TestInvoicingTracker(unittest.TestCase):
    INVOICES = [
        {"id": "INV-1", "customer": "Ana", "amount": 370,
         "due_date": "2026-11-10", "status": "unpaid"},
        {"id": "INV-2", "customer": "Bob", "amount": 200,
         "due_date": "2026-11-25", "status": "unpaid"},
        {"id": "INV-3", "customer": "Cid", "amount": 150,
         "due_date": "2026-11-01", "status": "paid"},
        {"id": "INV-4", "customer": "Dee", "amount": 500,
         "due_date": "2026-11-05", "status": "disputed"},
    ]

    def test_overdue_and_disputed(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("shared", tmp)
            res = invoicing_tracker.run(ctx, {
                "invoices": self.INVOICES, "date": "2026-11-20"})
            self.assertEqual(res["status"], "held")
            data = read_json(res["outputs"][0])
            self.assertEqual(data["total_owed"], 1070)
            self.assertEqual(data["overdue_total"], 370)
            self.assertEqual([r["id"] for r in data["overdue"]], ["INV-1"])
            self.assertEqual([r["id"] for r in data["due_soon"]], ["INV-2"])
            self.assertEqual([r["id"] for r in data["disputed"]], ["INV-4"])
            self.assertEqual(data["paid_total"], 150)

    def test_all_paid_is_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("shared", tmp)
            res = invoicing_tracker.run(ctx, {
                "invoices": [{"id": "INV-1", "customer": "Ana",
                              "amount": 100, "due_date": "2026-11-20",
                              "status": "paid"}],
                "date": "2026-11-20"})
            self.assertEqual(res["status"], "ok")
            data = read_json(res["outputs"][0])
            self.assertEqual(data["total_owed"], 0)

    def test_no_invoices_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("shared", tmp)
            res = invoicing_tracker.run(ctx, {"date": "2026-11-20"})
            self.assertEqual(res["status"], "held")


# --------------------------------------------------- finance controller ---

class TestFinanceController(unittest.TestCase):
    def test_revenue_margin_and_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("shared", tmp)
            res = finance_controller.run(ctx, {
                "invoices": [
                    {"id": "INV-1", "business": "gutter-genie",
                     "amount": 370, "status": "paid"},
                    {"id": "INV-2", "business": "gutter-genie",
                     "amount": 200, "status": "unpaid"},
                ],
                "expenses": [{"amount": 100, "category": "fuel",
                              "business": "gutter-genie"}],
                "targets": {"revenue_target": 1000, "margin_floor": 0.5},
                "date": "2026-11-20",
            })
            self.assertEqual(res["status"], "held")
            data = read_json(res["outputs"][0])
            self.assertEqual(data["revenue"], 370)
            self.assertEqual(data["costs"], 100)
            self.assertEqual(data["profit"], 270)
            self.assertAlmostEqual(data["margin"], 0.7297, places=4)
            self.assertEqual(len(data["flags"]), 1)
            self.assertIn("below target", data["flags"][0])

    def test_payments_fallback_when_no_paid_invoices(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("shared", tmp)
            res = finance_controller.run(ctx, {
                "payments": [{"amount": 250, "invoice_id": "INV-1"}],
                "date": "2026-11-20",
            })
            self.assertEqual(res["status"], "ok")
            data = read_json(res["outputs"][0])
            self.assertEqual(data["revenue"], 250)

    def test_business_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("gutter-genie", tmp)
            res = finance_controller.run(ctx, {
                "invoices": [
                    {"id": "INV-1", "business": "gutter-genie",
                     "amount": 100, "status": "paid"},
                    {"id": "INV-2", "business": "bnb-media",
                     "amount": 999, "status": "paid"},
                ],
                "date": "2026-11-20",
            })
            data = read_json(res["outputs"][0])
            self.assertEqual(data["revenue"], 100)

    def test_spend_request_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("shared", tmp)
            res = finance_controller.run(ctx, {
                "action": "spend $500 on ads",
                "invoices": [], "date": "2026-11-20"})
            self.assertEqual(res["status"], "held")
            self.assertEqual(res["outputs"], [])
            self.assertIn("Jay", res["summary"])

    def test_no_data_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("shared", tmp)
            res = finance_controller.run(ctx, {"date": "2026-11-20"})
            self.assertEqual(res["status"], "held")


# ------------------------------------------------------- quality control ---

class TestQualityControl(unittest.TestCase):
    CLEAN = {
        "to": "ana@example.ca", "from": "guttergeniewpg@gmail.com",
        "subject": "Your Gutter Genie estimate",
        "body": "Hi Ana, here is your estimate. Call or text 204-972-0325.",
        "channel": "email", "business": "gutter-genie",
    }

    def test_clean_draft_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("gutter-genie", tmp)
            res = quality_control.run(ctx, {"drafts": [self.CLEAN],
                                            "date": "2026-11-20"})
            self.assertEqual(res["status"], "ok")
            data = read_json(res["outputs"][0])
            self.assertTrue(data["all_passed"])

    def test_ai_label_and_em_dash_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("gutter-genie", tmp)
            bad = dict(self.CLEAN)
            bad["body"] = ("Hi Ana, this estimate was made by AI. Call or "
                           "text 204-972-0325 \u2014 thanks.")
            res = quality_control.run(ctx, {"drafts": [bad],
                                            "date": "2026-11-20"})
            self.assertEqual(res["status"], "held")
            data = read_json(res["outputs"][0])
            fixes = data["failed"][0]["fixes"]
            self.assertTrue(any("AI-label" in f for f in fixes))
            self.assertTrue(any("em dash" in f for f in fixes))

    def test_missing_phone_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("gutter-genie", tmp)
            bad = dict(self.CLEAN)
            bad["body"] = "Hi Ana, here is your estimate."
            res = quality_control.run(ctx, {"drafts": [bad],
                                            "date": "2026-11-20"})
            data = read_json(res["outputs"][0])
            self.assertTrue(any("phone" in f
                                for f in data["failed"][0]["fixes"]))

    def test_bnb_needs_no_phone(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("bnb-media", tmp)
            draft = {"to": "host@example.ca",
                     "from": "bnbmediawpg@gmail.com",
                     "subject": "Video offer",
                     "body": "Hi, we make walkthrough videos from your "
                             "listing photos.",
                     "channel": "email", "business": "bnb-media",
                     "source_tag": "airbnb"}
            res = quality_control.run(ctx, {"drafts": [draft],
                                            "date": "2026-11-20"})
            self.assertEqual(res["status"], "ok")

    def test_gutter_quote_math_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("gutter-genie", tmp)
            quote = (
                "GUTTER GENIE - ESTIMATE\n\nJOB SCOPE\n1. Clean gutters\n\n"
                "ESTIMATE BREAKDOWN\n"
                "Gutter cleaning (100 ft @ $1.75/ft): $175.00\n"
                "Base fee: $100.00\n"
                "------------------------------\n"
                "ESTIMATED TOTAL: $370.00\n\n"
                "PAYMENT TERMS: 30% deposit ($111.00), balance on "
                "completion. No tax.\n"
                "Call or text 204-972-0325.")
            res = quality_control.run(ctx, {
                "drafts": [dict(self.CLEAN, body=quote)],
                "date": "2026-11-20"})
            data = read_json(res["outputs"][0])
            self.assertEqual(len(data["failed"]), 1)
            self.assertTrue(any("sum to $275.00" in f
                                for f in data["failed"][0]["fixes"]))

    def test_single_text_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("gutter-genie", tmp)
            res = quality_control.run(ctx, {
                "text": "Call or text 204-972-0325.",
                "business": "gutter-genie", "channel": "email",
                "date": "2026-11-20"})
            self.assertEqual(res["status"], "ok")

    def test_nothing_to_review_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("shared", tmp)
            res = quality_control.run(ctx, {"date": "2026-11-20"})
            self.assertEqual(res["status"], "held")


# ----------------------------------------------------- lead source hunter ---

def fake_search_factory(results_by_query):
    def fake_search(query, max_results=10):
        return list(results_by_query.get(query, []))
    return fake_search


class TestLeadSourceHunter(unittest.TestCase):
    RESULTS = {
        "Winnipeg property management companies": [
            {"name": "Winnipeg PM Directory",
             "url": "https://example-pm.ca/directory",
             "snippet": "A directory of property managers in Winnipeg."},
            {"name": "Integrity Roofing",
             "url": "https://integrityroofinginc.ca/",
             "snippet": "Roofing company."},
            {"name": "Leads Pro",
             "url": "https://leadspro.example/pricing",
             "snippet": "Buy leads, see our pricing plans."},
        ],
    }

    def test_dedupes_drops_blocked_flags_paid(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx(
                "gutter-genie", tmp,
                search=fake_search_factory(self.RESULTS))
            res = lead_source_hunter.run(ctx, {
                "queries": ["Winnipeg property management companies"],
                "date": "2026-11-20"})
            self.assertEqual(res["status"], "held")  # paid flag held
            data = read_json(res["outputs"][0])
            urls = [s["url"] for s in data["sources"]]
            self.assertEqual(urls, ["https://example-pm.ca/directory",
                                    "https://leadspro.example/pricing"])
            self.assertEqual(data["paid_or_signup_flags"], 1)
            self.assertIn("directory", data["sources"][0]["notes"][0])

    def test_no_results_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("gutter-genie", tmp,
                           search=fake_search_factory({}))
            res = lead_source_hunter.run(ctx, {
                "queries": ["nothing matches this"],
                "date": "2026-11-20"})
            self.assertEqual(res["status"], "held")
            data = read_json(res["outputs"][0])
            self.assertEqual(data["sources"], [])

    def test_skip_web_never_calls_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            # No search wired in ctx: skip_web must fall back to an
            # empty search instead of hitting the network.
            ctx = make_ctx("gutter-genie", tmp)
            res = lead_source_hunter.run(ctx, {
                "queries": ["Winnipeg property management companies"],
                "skip_web": True, "date": "2026-11-20"})
            self.assertEqual(res["status"], "held")
            data = read_json(res["outputs"][0])
            self.assertEqual(data["sources"], [])


# ---------------------------------------------------------- ads manager ---

class TestAdsManager(unittest.TestCase):
    def test_plan_written_no_spend(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("gutter-genie", tmp)
            res = ads_manager.run(ctx, {
                "platform": "facebook", "budget": 200,
                "objective": "leads", "duration_days": 14,
                "date": "2026-11-20"})
            self.assertEqual(res["status"], "ok")
            with open(res["outputs"][0]) as fh:
                text = fh.read()
            self.assertIn("NO SPEND COMMITTED", text)
            self.assertIn("$200.00", text)

    def test_www_organic_only_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("winnipeg-web-works", tmp)
            res = ads_manager.run(ctx, {
                "platform": "facebook", "budget": 200,
                "date": "2026-11-20"})
            self.assertEqual(res["status"], "held")
            self.assertEqual(res["outputs"], [])

    def test_launch_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("gutter-genie", tmp)
            res = ads_manager.run(ctx, {
                "platform": "facebook", "budget": 200, "launch": True,
                "date": "2026-11-20"})
            self.assertEqual(res["status"], "held")
            self.assertEqual(res["outputs"], [])

    def test_performance_summary_math(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("gutter-genie", tmp)
            res = ads_manager.run(ctx, {
                "platform": "facebook", "budget": 200,
                "performance": [
                    {"platform": "facebook", "spend": 50,
                     "impressions": 1000, "clicks": 25},
                    {"platform": "facebook", "spend": 50,
                     "impressions": 1000, "clicks": 25},
                ],
                "date": "2026-11-20"})
            self.assertEqual(res["status"], "ok")
            self.assertEqual(len(res["outputs"]), 2)
            data = read_json(res["outputs"][1])
            self.assertEqual(data["spend"], 100)
            self.assertEqual(data["clicks"], 50)
            self.assertEqual(data["ctr"], 2.5)
            self.assertEqual(data["cpc"], 2.0)


# ------------------------------------------------------- customer support ---

class TestCustomerSupport(unittest.TestCase):
    def test_question_gets_plain_reply(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("gutter-genie", tmp)
            res = customer_support.run(ctx, {
                "messages": [{"from": "ana@example.ca",
                              "subject": "When can you come?",
                              "body": "When are you available next week?"}],
                "date": "2026-11-20"})
            self.assertEqual(res["status"], "ok")
            data = read_json(res["outputs"][0])
            reply = data["replies"][0]
            self.assertEqual(reply["classification"], "question")
            self.assertIn("204-972-0325", reply["draft"])
            self.assertNotIn("\u2014", reply["draft"])

    def test_complaint_escalates_to_jay(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("gutter-genie", tmp)
            res = customer_support.run(ctx, {
                "messages": [{"from": "bob@example.ca",
                              "subject": "Bad job",
                              "body": "Your crew damaged my downspout and "
                                      "I am unhappy."}],
                "date": "2026-11-20"})
            self.assertEqual(res["status"], "held")
            data = read_json(res["outputs"][0])
            self.assertEqual(data["replies"][0]["classification"],
                             "complaint")
            self.assertTrue(data["replies"][0]["escalated"])
            self.assertEqual(len(data["escalations"]), 1)

    def test_dispute_and_out_of_scope_escalate(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("move-and-metal", tmp)
            res = customer_support.run(ctx, {
                "messages": [
                    {"from": "a@example.ca", "subject": "Price",
                     "body": "I want a refund, you overcharged me."},
                    {"from": "b@example.ca", "subject": "Roof",
                     "body": "Can you do a full roof replacement?"},
                ],
                "date": "2026-11-20"})
            self.assertEqual(res["status"], "held")
            data = read_json(res["outputs"][0])
            kinds = [r["classification"] for r in data["replies"]]
            self.assertEqual(kinds, ["dispute", "out-of-scope"])

    def test_bnb_reply_has_no_phone(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("bnb-media", tmp)
            res = customer_support.run(ctx, {
                "messages": [{"from": "host@example.ca",
                              "subject": "Video",
                              "body": "When will my video be ready?"}],
                "date": "2026-11-20"})
            data = read_json(res["outputs"][0])
            self.assertNotIn("204-972-0325", data["replies"][0]["draft"])

    def test_job_match_noted(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("gutter-genie", tmp)
            res = customer_support.run(ctx, {
                "messages": [{"from": "ana@example.ca",
                              "subject": "Update",
                              "body": "How is my job going?"}],
                "jobs": [{"customer": "Ana", "email": "ana@example.ca",
                          "job": "Gutter cleaning", "status": "booked"}],
                "date": "2026-11-20"})
            data = read_json(res["outputs"][0])
            self.assertTrue(data["replies"][0]["job_matched"])

    def test_no_messages_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("gutter-genie", tmp)
            res = customer_support.run(ctx, {"date": "2026-11-20"})
            self.assertEqual(res["status"], "held")


# ------------------------------------------------------- review requester ---

class TestReviewRequester(unittest.TestCase):
    JOBS = [
        {"customer": "Ana", "email": "ana@example.ca",
         "job": "Gutter cleaning", "paid": True, "happy": True},
        {"customer": "Bob", "email": "bob@example.ca",
         "job": "Guard install", "paid": True, "happy": False},
        {"customer": "Cid", "email": "cid@example.ca",
         "job": "Downspout flush", "paid": False, "happy": True},
    ]

    def test_only_happy_paid_get_asked(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("gutter-genie", tmp)
            res = review_requester.run(ctx, {
                "jobs": self.JOBS, "date": "2026-11-20"})
            self.assertEqual(res["status"], "held")  # unhappy escalates
            data = read_json(res["outputs"][0])
            self.assertEqual([r["customer"] for r in data["requests"]],
                             ["Ana"])
            self.assertEqual(len(data["skipped"]), 1)
            self.assertEqual(len(data["escalations"]), 1)
            self.assertIn("204-972-0325", data["requests"][0]["draft"])

    def test_all_happy_paid_is_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("gutter-genie", tmp)
            res = review_requester.run(ctx, {
                "jobs": [self.JOBS[0]], "date": "2026-11-20"})
            self.assertEqual(res["status"], "ok")

    def test_no_jobs_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("gutter-genie", tmp)
            res = review_requester.run(ctx, {"date": "2026-11-20"})
            self.assertEqual(res["status"], "held")


# ----------------------------------------------------- bnb video producer ---

def listing(photos=6, tier="basic", title="Lakefront Airbnb"):
    return {"title": title, "url": "https://airbnb.example/rooms/1",
            "tier": tier,
            "photos": [{"url": "https://img.example/%d.jpg" % i,
                        "caption": "Photo %d" % i}
                       for i in range(photos)]}


class TestBnbVideoProducer(unittest.TestCase):
    def test_basic_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("bnb-media", tmp)
            res = bnb_video_producer.run(ctx, {
                "listing": listing(photos=6, tier="basic"),
                "date": "2026-11-20"})
            self.assertEqual(res["status"], "ok")
            data = read_json(res["outputs"][0])
            plan = data["plans"][0]
            self.assertEqual(plan["price"], 199.0)
            self.assertEqual(plan["photos_used"], 6)
            self.assertEqual(len(plan["shots"]), 8)  # intro + 6 + outro
            self.assertEqual(plan["duration_seconds"], 3 + 6 * 4 + 4)
            self.assertNotIn("204-972-0325",
                             json.dumps(plan))

    def test_premium_pricing_and_timing(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("bnb-media", tmp)
            res = bnb_video_producer.run(ctx, {
                "listing": listing(photos=8, tier="premium"),
                "date": "2026-11-20"})
            plan = read_json(res["outputs"][0])["plans"][0]
            self.assertEqual(plan["price"], 299.0)
            self.assertEqual(plan["duration_seconds"], 3 + 8 * 5 + 4)

    def test_too_few_photos_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("bnb-media", tmp)
            res = bnb_video_producer.run(ctx, {
                "listing": listing(photos=3),
                "date": "2026-11-20"})
            self.assertEqual(res["status"], "held")
            data = read_json(res["outputs"][0])
            self.assertEqual(data["plans"], [])
            self.assertTrue(any("only 3 usable" in r
                                for r in data["held_reasons"]))

    def test_venue_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("bnb-media", tmp)
            res = bnb_video_producer.run(ctx, {
                "listing": listing(title="Grand Wedding Venue"),
                "date": "2026-11-20"})
            self.assertEqual(res["status"], "held")
            data = read_json(res["outputs"][0])
            self.assertEqual(data["plans"], [])

    def test_no_listing_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("bnb-media", tmp)
            res = bnb_video_producer.run(ctx, {"date": "2026-11-20"})
            self.assertEqual(res["status"], "held")


# ---------------------------------------------------------- ops manager ---

class TestOpsManager(unittest.TestCase):
    def test_plan_written_from_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("shared", tmp)
            res = ops_manager.run(ctx, {"date": "2026-11-20"})
            # First run of the day: nothing is stalled yet, plan is ok.
            self.assertEqual(res["status"], "ok")
            with open(res["outputs"][0]) as fh:
                text = fh.read()
            self.assertIn("OPS PLAN - 2026-11-20", text)
            self.assertIn("50 working agents", text)
            self.assertIn("STILL PLAN-MODE (0)", text)
            self.assertIn("No runs yet today", text)
            self.assertIn("dry-run only", text)

    def test_health_from_runs_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs_file = os.path.join(tmp, "runs.jsonl")
            with open(runs_file, "w") as fh:
                fh.write(json.dumps(
                    {"date": "2026-11-20", "agent": "reporting",
                     "business": "shared", "status": "ok",
                     "summary": "report done"}) + "\n")
                fh.write(json.dumps(
                    {"date": "2026-11-20", "agent": "lead_researcher",
                     "business": "gutter-genie", "status": "error",
                     "summary": "boom"}) + "\n")
            ctx = make_ctx("shared", tmp)
            res = ops_manager.run(ctx, {"runs_file": runs_file,
                                        "date": "2026-11-20"})
            self.assertEqual(res["status"], "held")
            with open(res["outputs"][0]) as fh:
                text = fh.read()
            self.assertIn("BLOCKED (1): lead_researcher", text)
            self.assertIn("reporting: ok (runs: 1)", text)

    def test_live_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = make_ctx("shared", tmp)
            res = ops_manager.run(ctx, {"mode": "live",
                                        "date": "2026-11-20"})
            self.assertEqual(res["status"], "held")
            self.assertEqual(res["outputs"], [])


# ------------------------------------------- supervisor wiring (all 50) ---

NEW_AGENT_IDS = [
    ("finance-controller", "shared"),
    ("quality-control", "shared"),
    ("lead-source-hunter", "shared"),
    ("ads-manager", "shared"),
    ("invoicing-tracker", "shared"),
    ("bookkeeper", "shared"),
    ("gg-customer-support", "gutter-genie"),
    ("www-customer-support", "winnipeg-web-works"),
    ("mm-customer-support", "move-and-metal"),
    ("bnb-customer-support", "bnb-media"),
    ("db-customer-support", "demoboss"),
    ("gg-review-requester", "gutter-genie"),
    ("bnb-video-producer", "bnb-media"),
    ("ops-manager", "shared"),
]


class TestSupervisorWiring(unittest.TestCase):
    def _supervisor_in_tmp(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        os.makedirs(os.path.join(tmp, "agents"))
        os.makedirs(os.path.join(tmp, "config"))
        shutil.copy(os.path.join(repo, "agents", "registry.yaml"),
                    os.path.join(tmp, "agents", "registry.yaml"))
        shutil.copy(os.path.join(repo, "config", "businesses.yaml"),
                    os.path.join(tmp, "config", "businesses.yaml"))
        return Supervisor(root=tmp)

    def test_registry_has_50_working_zero_defined(self):
        sup = self._supervisor_in_tmp()
        registry = sup.load_registry()
        self.assertEqual(len(registry), 50)
        self.assertEqual(
            sum(1 for e in registry if e.get("status") == "working"), 50)
        self.assertEqual(
            sum(1 for e in registry if e.get("status") == "defined"), 0)
        self.assertTrue(all(e.get("module") for e in registry))

    def test_new_agents_run_not_planned(self):
        sup = self._supervisor_in_tmp()
        for agent_id, business in NEW_AGENT_IDS:
            with self.subTest(agent=agent_id):
                result = sup.run(
                    {"agent": agent_id, "business": business,
                     "task": {"mode": "daily"}},
                    dry_run=True, date="2026-11-20")
                self.assertNotEqual(result["status"], "planned",
                                    "%s still plan-mode" % agent_id)
                self.assertNotEqual(result["status"], "error",
                                    "%s errored: %s" % (
                                        agent_id, result.get("summary")))

    def test_ops_manager_runs_via_supervisor(self):
        sup = self._supervisor_in_tmp()
        result = sup.run(
            {"agent": "ops-manager", "business": "shared",
             "task": {"mode": "daily"}},
            dry_run=True, date="2026-11-20")
        self.assertIn(result["status"], ("ok", "held"))
        self.assertTrue(result["outputs"])
        with open(result["outputs"][0]) as fh:
            self.assertIn("OPS PLAN", fh.read())


if __name__ == "__main__":
    unittest.main()
