"""Tests for set-A agents (W3): lead_researcher, outreach_sender,
followup_chaser. Stdlib unittest only. No network: web search is skipped
and the Gmail CLI is never invoked (follow-up test uses fake state only).

NOTE: core/guard.py did not exist when these tests were written (parallel
worker), so the tests use a local stub guard that mimics the real rules
minimally (reject info@/generic role mail, reject Integrity Roofing,
reject wrong-from). Swap in the real guard when it lands.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents import lead_researcher, outreach_sender, followup_chaser  # noqa: E402


class FakeState:
    def __init__(self):
        self.store = {}
        self.logs = []

    def get(self, key, default=None):
        return self.store.get(key, default)

    def set(self, key, value):
        self.store[key] = value

    def append_log(self, message):
        self.logs.append(message)


def stub_guard(draft):
    """Minimal mimic of the real guard rules."""
    to = (draft.get("to") or "").lower()
    body = (draft.get("body") or "").lower()
    if to.startswith("info@"):
        return False, ["generic role address not allowed"]
    if "integrity roofing" in body or "integrityroofing" in to:
        return False, ["never contact Integrity Roofing"]
    if draft.get("from") and draft.get("from") != draft.get("_expected_from", draft.get("from")):
        return False, ["sender mismatch"]
    return True, []


def make_ctx(business, out_dir, state=None):
    return {
        "business": business,
        "dry_run": True,
        "state": state if state is not None else FakeState(),
        "bro": lambda prompt: None,
        "guard": stub_guard,
        "out_dir": out_dir,
    }


GG = {"slug": "gutter-genie", "name": "Gutter Genie",
      "email": "guttergeniewpg@gmail.com", "provider": "gmail"}
MM = {"slug": "move-and-metal", "name": "Move & Metal",
      "email": "metalmoovers@hotmail.com", "provider": "outlook"}

LEADS_MD = """# Test leads

## 1. Fresh Shop
- Contact: Ana Fresh
- Email: ana@freshshop.ca
- Source: https://freshshop.ca/ (own site)

## 2. Already Used Co
- Contact: Used Person
- Email: used@oldco.ca
- Source: https://oldco.ca/

## 3. Info Shop
- Contact: Info Person
- Email: info@infoshop.ca
- Source: https://infoshop.ca/

## 4. Integrity Roofing
- Contact: Bad Actor
- Email: bob@integrityroofinginc.ca
- Source: https://integrityroofinginc.ca/
"""


class TestLeadResearcher(unittest.TestCase):
    def test_dedupes_and_drops_info(self):
        with tempfile.TemporaryDirectory() as tmp:
            leads_file = os.path.join(tmp, "gutter-genie.md")
            with open(leads_file, "w") as fh:
                fh.write(LEADS_MD)
            ctx = make_ctx(GG, tmp)
            res = lead_researcher.run(ctx, {
                "skip_web": True,
                "local_leads_file": leads_file,
                "used_emails": {"used@oldco.ca"},
                "used_emails_path": os.path.join(tmp, "nonexistent-used.txt"),
            })
            self.assertEqual(res["status"], "ok")
            self.assertEqual(len(res["outputs"]), 1)
            with open(res["outputs"][0]) as fh:
                leads = json.load(fh)
            emails = [l["email"] for l in leads]
            # Only the fresh lead survives.
            self.assertEqual(emails, ["ana@freshshop.ca"])
            self.assertIn("found 4, new 1", res["summary"])
            # Saved to state too.
            self.assertEqual([l["email"] for l in ctx["state"].get("leads")],
                             ["ana@freshshop.ca"])

    def test_bnb_keeps_only_listings(self):
        with tempfile.TemporaryDirectory() as tmp:
            leads_file = os.path.join(tmp, "bnb-media.md")
            with open(leads_file, "w") as fh:
                fh.write(
                    "## 1. Lake Lodge\n- Email: lodge@lake.ca\n"
                    "- Source: https://www.travelmanitoba.com/directory/lake-lodge/\n\n"
                    "## 2. Random Store\n- Email: shop@random.ca\n"
                    "- Source: https://random.ca/\n")
            bnb = {"slug": "bnb-media", "name": "Bnb Media",
                   "email": "bnbmediawpg@gmail.com", "provider": "gmail"}
            ctx = make_ctx(bnb, tmp)
            res = lead_researcher.run(ctx, {
                "skip_web": True, "local_leads_file": leads_file,
                "used_emails": set(),
                "used_emails_path": os.path.join(tmp, "nonexistent.txt"),
            })
            with open(res["outputs"][0]) as fh:
                leads = json.load(fh)
            self.assertEqual([l["email"] for l in leads], ["lodge@lake.ca"])


class TestOutreachSender(unittest.TestCase):
    def test_holds_outlook_business(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = FakeState()
            state.set("leads", [
                {"name": "Ana Fresh", "email": "ana@freshshop.ca",
                 "source": "test", "source_tag": "local", "niche": "x"}])
            ctx = make_ctx(MM, tmp, state)
            res = outreach_sender.run(ctx, {})
            self.assertEqual(res["status"], "held")
            with open(res["outputs"][0]) as fh:
                drafts = json.load(fh)
            self.assertEqual(len(drafts), 1)
            self.assertEqual(drafts[0]["status"], "held")
            self.assertIn("Outlook reauth needed", drafts[0]["held_reasons"])
            self.assertIn("0 drafted, 1 held", res["summary"])

    def test_holds_info_recipient_and_drafts_good_lead(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = FakeState()
            state.set("leads", [
                {"name": "Ana Fresh", "email": "ana@freshshop.ca",
                 "source": "test", "source_tag": "local", "niche": "x"},
                {"name": "Info Shop", "email": "info@infoshop.ca",
                 "source": "test", "source_tag": "local", "niche": "x"},
            ])
            ctx = make_ctx(GG, tmp, state)
            res = outreach_sender.run(ctx, {})
            with open(res["outputs"][0]) as fh:
                drafts = json.load(fh)
            by_to = {d["to"]: d for d in drafts}
            self.assertEqual(by_to["ana@freshshop.ca"]["status"], "draft")
            self.assertEqual(by_to["info@infoshop.ca"]["status"], "held")
            self.assertIn("1 drafted, 1 held", res["summary"])
            # GG first outreach: no pricing, phone present.
            self.assertNotIn("$", by_to["ana@freshshop.ca"]["body"])
            self.assertIn("204-972-0325", by_to["ana@freshshop.ca"]["body"])

    def test_drip_no_inbox_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = FakeState()
            state.set("leads", [{"name": "X", "email": "x@shop.ca",
                                 "source": "t", "source_tag": "local", "niche": "x"}])
            drip = {"slug": "drip-detailing", "name": "Drip Detailing",
                    "email": None, "provider": "none"}
            ctx = make_ctx(drip, tmp, state)
            res = outreach_sender.run(ctx, {})
            with open(res["outputs"][0]) as fh:
                drafts = json.load(fh)
            self.assertEqual(drafts[0]["status"], "held")
            self.assertIn("no inbox yet", drafts[0]["held_reasons"])


class TestFollowupChaser(unittest.TestCase):
    def test_followups_from_fake_state_no_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = FakeState()
            state.set("followup_queue", [
                {"email": "sara@wpganchor.ca", "contact": "Sara Martin",
                 "last_contact": "2026-11-13"},
            ])
            # Empty SENT.md so only the state queue contributes.
            sent_md = os.path.join(tmp, "SENT.md")
            with open(sent_md, "w") as fh:
                fh.write("# Sent\n")
            ctx = make_ctx(GG, tmp, state)
            res = followup_chaser.run(ctx, {"sent_md_path": sent_md,
                                            "skip_gmail": True})
            self.assertEqual(res["status"], "ok")
            with open(res["outputs"][0]) as fh:
                items = json.load(fh)
            self.assertEqual(len(items), 1)
            item = items[0]
            self.assertEqual(item["thread"], "sara@wpganchor.ca")
            self.assertEqual(item["contact"], "Sara Martin")
            self.assertGreaterEqual(item["days_waiting"], 3)
            self.assertIn("Send follow-up now", item["suggested_next_step"])
            self.assertIn("free look", item["draft_reply"])
            self.assertIn("204-972-0325", item["draft_reply"])

    def test_outlook_followups_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = FakeState()
            state.set("followup_queue", [
                {"email": "a@b.ca", "contact": "A B",
                 "last_contact": "2026-11-10"},
            ])
            sent_md = os.path.join(tmp, "SENT.md")
            with open(sent_md, "w") as fh:
                fh.write("# Sent\n")
            ctx = make_ctx(MM, tmp, state)
            res = followup_chaser.run(ctx, {"sent_md_path": sent_md,
                                            "skip_gmail": True})
            self.assertEqual(res["status"], "held")
            self.assertIn("Outlook reauth needed", res["held"])
            with open(res["outputs"][0]) as fh:
                items = json.load(fh)
            self.assertEqual(items[0]["status"], "held")


if __name__ == "__main__":
    unittest.main()
