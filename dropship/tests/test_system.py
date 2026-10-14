import io
import json
import sqlite3
import unittest
import urllib.error
from datetime import date
from unittest import mock

from dropship import util
from dropship.config import Settings, SpendingLimits
from dropship.economics import contribution, suggested_price
from dropship.fulfillment.orders import FulfillmentPipeline, InvalidTransition, OrderStore
from dropship.fulfillment.printful_supplier import PrintfulSupplier, SimulatedSupplier
from dropship.marketing import content
from dropship.reporting import dashboard
from dropship.research import candidates as cand
from dropship.support.assistant import Policy, SupportAssistant

ORDER = {
    "email": "buyer@example.com",
    "recipient": {"name": "Test Buyer", "address1": "1 Main St", "city": "LA", "state_code": "CA",
                  "country_code": "US", "zip": "90001"},
    "items": [{"supplier_variant_id": 4012, "quantity": 2}],
    "expected_supplier_cost": 24.0,
}

APPROVED = Settings(live_orders_enabled=True,
                    limits=SpendingLimits(max_supplier_order_usd=50, max_daily_supplier_spend_usd=60))


def pipeline(settings=Settings(), supplier=None, alerts=None):
    store = OrderStore(sqlite3.connect(":memory:"))
    sup = supplier or SimulatedSupplier(unit_cost=12.0)
    return store, sup, FulfillmentPipeline(store, sup, settings, alert=(alerts.append if alerts is not None else None))


class EconomicsTest(unittest.TestCase):
    def test_contribution_formula(self):
        e = contribution(30.0, 12.0, 5.0, ad_cost_per_order=4.0)
        fees = 30 * 0.029 + 0.30
        refunds = 0.05 * 17
        self.assertAlmostEqual(e.contribution, round(30 - 12 - 5 - fees - refunds - 4, 2), places=2)
        self.assertEqual(e.label, "ESTIMATE")
        self.assertAlmostEqual(e.breakeven_cpa, e.contribution + 4, places=2)

    def test_suggested_price_meets_margin(self):
        p = suggested_price(12.0, 5.0, 0.4)
        self.assertGreaterEqual(contribution(p, 12, 5).breakeven_cpa / p, 0.4)
        self.assertTrue(f"{p:.2f}".endswith(".99"))

    def test_rejects_nonpositive_price(self):
        with self.assertRaises(ValueError):
            contribution(0, 1, 1)


class CandidatesTest(unittest.TestCase):
    def fake_product(self, _pid, stock=True):
        return {"product": {"title": "Tee"},
                "variants": [{"price": "10.00", "in_stock": stock, "availability_regions": {"US": "x"}}]}

    def test_unverified_demand_scores_zero_and_evidence_persists(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(cand.SCHEMA)
        spec = cand.Spec(1, "T-SHIRT", 4, 2, 1)
        row = cand.evaluate(spec, "n", fetch=self.fake_product)
        self.assertEqual(row["demand_evidence"], "unverified")
        cand.upsert(conn, row)
        base = cand.ranked(conn)[0]["score"]
        cand.record_demand(conn, row["key"], {"source": "test", "date": "2026-10-14", "metric": "x"}, 0.5)
        self.assertAlmostEqual(cand.ranked(conn)[0]["score"], base + 10, places=1)
        cand.upsert(conn, cand.evaluate(spec, "n", fetch=self.fake_product))  # catalog refresh
        self.assertNotEqual(cand.ranked(conn)[0]["demand_evidence"], "unverified")
        with self.assertRaises(ValueError):
            cand.record_demand(conn, row["key"], {"source": "x"}, 0.5)

    def test_out_of_stock_is_paused(self):
        row = cand.evaluate(cand.Spec(1, "T-SHIRT", 4, 2, 1), "n", fetch=lambda p: self.fake_product(p, False))
        self.assertEqual(row["status"], "paused_out_of_stock")
        self.assertEqual(row["score"], 0.0)


class FulfillmentTest(unittest.TestCase):
    def test_duplicate_ingest_ignored(self):
        store, _, _ = pipeline()
        self.assertTrue(store.ingest("1001", ORDER))
        self.assertFalse(store.ingest("1001", ORDER))

    def test_default_settings_never_charge(self):
        store, sup, p = pipeline()
        store.ingest("1001", ORDER)
        counts = p.run()
        self.assertEqual(counts["held"], 1)
        self.assertEqual(sup.confirm_calls, 0)
        self.assertEqual(store.get("1001")["state"], "submitted_draft")
        p.run()  # rerun must not create a second draft
        self.assertEqual(len(sup.drafts), 1)

    def test_confirm_within_limits(self):
        store, sup, p = pipeline(APPROVED)
        store.ingest("1001", ORDER)
        store.ingest("1002", ORDER)
        counts = p.run()  # 2 x 24 = 48, within the 60/day limit
        self.assertEqual(counts["confirmed"], 2)
        self.assertEqual(sup.confirm_calls, 2)

    def test_daily_cap_holds_excess(self):
        settings = Settings(live_orders_enabled=True, limits=SpendingLimits(50, 30))
        store, sup, p = pipeline(settings)
        store.ingest("1001", ORDER)
        store.ingest("1002", ORDER)
        p.run()
        self.assertEqual(sup.confirm_calls, 1)
        self.assertEqual(store.get("1002")["state"], "submitted_draft")

    def test_per_order_limit(self):
        settings = Settings(live_orders_enabled=True, limits=SpendingLimits(10, 100))
        store, sup, p = pipeline(settings)
        store.ingest("1001", ORDER)
        p.run()
        self.assertEqual(sup.confirm_calls, 0)

    def test_unavailable_sku_needs_review_no_substitution(self):
        alerts = []
        store, sup, p = pipeline(supplier=SimulatedSupplier(unavailable={4012}), alerts=alerts)
        store.ingest("1001", ORDER)
        p.run()
        self.assertEqual(store.get("1001")["state"], "needs_review")
        self.assertEqual(sup.drafts, {})
        self.assertTrue(alerts)

    def test_cost_spike_needs_review(self):
        alerts = []
        store, _, p = pipeline(supplier=SimulatedSupplier(unit_cost=20.0), alerts=alerts)
        store.ingest("1001", ORDER)
        p.run()
        self.assertEqual(store.get("1001")["state"], "needs_review")
        self.assertIn("exceeds expected", alerts[0])

    def test_invalid_transition(self):
        store, _, _ = pipeline()
        store.ingest("1001", ORDER)
        with self.assertRaises(InvalidTransition):
            store.transition("1001", "shipped")

    def test_end_to_end_tracking(self):
        store, sup, p = pipeline(APPROVED)
        store.ingest("1001", ORDER)
        p.run()
        sid = store.get("1001")["supplier_order_id"]
        sup.status[sid] = {"state": "in_production"}
        p.run()
        self.assertEqual(store.get("1001")["state"], "in_production")
        sup.status[sid] = {"state": "shipped", "tracking_number": "9400TEST", "tracking_url": "https://t/9400TEST", "carrier": "USPS"}
        p.run()
        o = store.get("1001")
        self.assertEqual((o["state"], o["tracking_number"]), ("shipped", "9400TEST"))
        sup.status[sid] = {"state": "delivered"}
        p.run()
        self.assertEqual(store.get("1001")["state"], "delivered")
        self.assertEqual(sup.confirm_calls, 1)

    def test_draft_failure_alerts(self):
        alerts = []
        sup = SimulatedSupplier()
        sup.create_draft = mock.Mock(side_effect=RuntimeError("boom"))
        store, _, p = pipeline(supplier=sup, alerts=alerts)
        store.ingest("1001", ORDER)
        p.run()
        self.assertEqual(store.get("1001")["state"], "failed")
        self.assertIn("boom", alerts[0])

    def test_printful_adapter_requests(self):
        calls = []

        def fake_http(url, method="GET", body=None, headers=None):
            calls.append((url, method, body, headers))
            if url.endswith("estimate-costs"):
                return {"result": {"costs": {"total": "27.50"}}}
            if "confirm=false" in url:
                return {"result": {"id": 77}}
            return {"result": {"status": "fulfilled", "shipments": [{"tracking_number": "T1", "tracking_url": "u", "carrier": "USPS"}]}}

        pf = PrintfulSupplier("secret-token", http=fake_http)
        self.assertEqual(pf.quote(ORDER).total_cost, 27.5)
        self.assertEqual(pf.create_draft(ORDER, "1001"), "77")
        self.assertEqual(calls[1][2]["external_id"], "1001")
        self.assertIn("confirm=false", calls[1][0])
        self.assertEqual(pf.get_status("77")["state"], "shipped")
        self.assertNotIn("secret-token", repr(pf.__dict__.get("_http")))


class SupportTest(unittest.TestCase):
    def setUp(self):
        self.store = OrderStore(sqlite3.connect(":memory:"))
        self.store.ingest("1001", ORDER)
        self.bot = SupportAssistant(Policy("Leaf & Line", "help@example.com", product_facts={"material": "100% cotton"}), self.store)

    def test_escalations(self):
        for msg in ["I'm filing a chargeback", "my lawyer will hear about this", "the candle burned my hand",
                    "this is a scam"]:
            self.assertTrue(self.bot.respond(msg).escalate, msg)

    def test_refund_requests_go_to_human(self):
        r = self.bot.respond("My mug arrived damaged, I want a refund")
        self.assertEqual(r.intent, "refund")
        self.assertTrue(r.escalate)

    def test_order_status_requires_matching_email(self):
        self.assertIn("couldn't match", self.bot.respond("where is order #1001", "other@example.com").text)
        r = self.bot.respond("where is order #1001", "buyer@example.com")
        self.assertIn("being produced", r.text)

    def test_never_invents_tracking(self):
        r = self.bot.respond("tracking for 1001?", "buyer@example.com")
        self.assertNotRegex(r.text, r"\d{10,}")

    def test_product_info_from_facts(self):
        self.assertIn("100% cotton", self.bot.respond("what material is it?").text)


class DashboardTest(unittest.TestCase):
    def test_unconnected_sources_are_unavailable(self):
        rep = dashboard.build(dashboard.Sources())
        self.assertEqual(rep["metrics"]["total_sales"]["value"], dashboard.UNAVAILABLE)
        self.assertEqual(rep["metrics"]["profit"]["value"], dashboard.UNAVAILABLE)

    def test_profit_when_all_connected(self):
        store, _, p = pipeline(APPROVED)
        store.ingest("1001", ORDER)
        p.run()
        src = dashboard.Sources(store_orders=[{"total": 60.0, "refunded": 0.0}], sessions=100, ad_spend=5.0)
        rep = dashboard.build(src, store)
        fees = round(60 * 0.029 + 0.30, 2)
        self.assertAlmostEqual(rep["metrics"]["profit"]["value"], round(60 - fees - 24 - 5, 2))
        self.assertEqual(rep["metrics"]["conversion_rate_pct"]["value"], 1.0)
        self.assertIn("Profit", dashboard.render_text(rep))


class MarketingTest(unittest.TestCase):
    def test_calendar_shape(self):
        plan = content.calendar(["Tee", "Mug"], "plant parent", ["#a", "#b", "#c", "#d"], date(2026, 10, 15), days=3)
        self.assertEqual(len(plan), 9)
        self.assertTrue(all(i["status"] == "draft_for_review" for i in plan))
        self.assertEqual(len(next(i for i in plan if i["platform"] == "tiktok")["hashtags"]), 3)

    def test_banned_claims(self):
        self.assertEqual(content.check_claims("Our best seller, only a few left!"), ["best seller", "only a few left"])

    def test_rank_formats_needs_real_metrics(self):
        plan = content.calendar(["Tee"], "x", [], date(2026, 10, 15), days=2, platforms=("tiktok",))
        a = content.record_performance(plan[0], 1000, 10, 1, 20, "tiktok analytics export")
        b = content.record_performance(plan[1], 1000, 10, 1, 5, "tiktok analytics export")
        self.assertEqual(content.rank_formats([a, b, plan[0]])[0][0], a["format"])
        with self.assertRaises(ValueError):
            content.record_performance(plan[0], 1, 1, 1, 1, "")


class UtilTest(unittest.TestCase):
    def test_redaction(self):
        s = util.redact('Authorization: Bearer abc.def-123 token=xyz shpat_ABC123 "api_key": "k1"')
        for secret in ("abc.def-123", "xyz", "shpat_ABC123", "k1"):
            self.assertNotIn(secret, s)

    def test_http_retries_then_raises(self):
        err = urllib.error.HTTPError("u", 503, "x", {}, io.BytesIO(b""))
        sleeps = []
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(urllib.error.HTTPError):
                util.http_json("https://x.test", retries=2, sleep=sleeps.append)
        self.assertEqual(sleeps, [2.0, 4.0])

    def test_http_no_retry_on_4xx(self):
        err = urllib.error.HTTPError("u", 401, "x", {}, io.BytesIO(b""))
        sleeps = []
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(urllib.error.HTTPError):
                util.http_json("https://x.test", sleep=sleeps.append)
        self.assertEqual(sleeps, [])


if __name__ == "__main__":
    unittest.main()
