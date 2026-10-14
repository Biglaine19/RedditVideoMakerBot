import io
import json
import sqlite3
import unittest
import urllib.error
from datetime import date
from unittest import mock

from dropship import util
from dropship.config import Settings, SpendingLimits
from dropship.channels import etsy
from dropship.economics import ETSY, contribution, suggested_price
from dropship.fulfillment.orders import FulfillmentPipeline, InvalidTransition, OrderStore
from dropship.fulfillment.printful_supplier import PrintfulSupplier, SimulatedSupplier
from dropship.fulfillment.printify_supplier import PrintifySupplier
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

    def test_etsy_fees(self):
        e = contribution(30.0, 12.0, 4.0, fees=ETSY)
        self.assertAlmostEqual(e.payment_fees, round(30 * 0.03 + 0.25, 2))
        self.assertAlmostEqual(e.channel_fees, round(30 * 0.065 + 0.20, 2))
        from dataclasses import replace
        offsite = contribution(30.0, 12.0, 4.0, fees=replace(ETSY, offsite_ads_share=1.0))
        self.assertAlmostEqual(e.contribution - offsite.contribution, 4.5, places=2)

    def test_rejects_nonpositive_price(self):
        with self.assertRaises(ValueError):
            contribution(0, 1, 1)


class CandidatesTest(unittest.TestCase):
    def fake_product(self, _pid, stock=True):
        return {"product": {"title": "Tee"},
                "variants": [{"price": "10.00", "in_stock": stock, "availability_regions": {"US": "x"}}]}

    def test_unverified_demand_scores_zero_and_evidence_persists(self):
        conn = cand.init(sqlite3.connect(":memory:"))
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


class PrintifyResearchTest(unittest.TestCase):
    def kwargs(self, cost=(10.0, "owner-entered: invoice (2026-10-14)")):
        return dict(shipping=lambda b: {"first_item": 4.0, "handling_days_max": 10, "source": "live"},
                    variant_list=lambda b: [{"id": 1}], resolve=lambda b, pf_id, m: cost,
                    bp=lambda b: {"title": "Tee", "brand": "B", "model": "M"})

    def test_verified_cost(self):
        row = cand.evaluate_printify(cand.Spec(12, "T-SHIRT", 4, 2, 1), "n", {}, **self.kwargs())
        self.assertEqual((row["status"], row["channel"]), ("candidate", "etsy"))
        self.assertLess(row["contribution_if_offsite_ad"], row["contribution_before_ads"])

    def test_benchmark_cost_flagged(self):
        row = cand.evaluate_printify(cand.Spec(12, "T-SHIRT", 4, 2, 1), "n", {},
                                     **self.kwargs((10.0, "BENCHMARK: printful")))
        self.assertEqual(row["status"], "cost_unverified")

    def test_missing_cost_not_priced(self):
        conn = cand.init(sqlite3.connect(":memory:"))
        row = cand.evaluate_printify(cand.Spec(6, "T-SHIRT", 4, 2, 1), "n", {}, **self.kwargs((None, "unknown")))
        self.assertEqual((row["status"], row["price"], row["score"]), ("needs_cost", None, 0.0))
        cand.upsert(conn, row)
        with self.assertRaises(ValueError):
            cand.record_demand(conn, row["key"], {"source": "s", "date": "d", "metric": "m"}, 0.5)

    def test_manual_costs_require_source(self):
        import tempfile
        from pathlib import Path
        from dropship.research import printify_catalog as pfy
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "c.json"
            path.write_text(json.dumps({"12": {"cost": 9.5}}))
            with self.assertRaises(ValueError):
                pfy.load_manual_costs(path)


class FakePrintify:
    """Records calls to a fake Printify API."""

    def __init__(self, orders=None, status="on-hold", shipments=None):
        self.calls = []
        self.orders = orders or []
        self.status = status
        self.shipments = shipments or []

    def __call__(self, url, method="GET", body=None, headers=None):
        self.calls.append((method, url, body))
        if "/products/" in url:
            return {"variants": [{"id": 11, "cost": 1050, "is_enabled": True, "is_available": True},
                                 {"id": 12, "cost": 1050, "is_enabled": False}]}
        if url.endswith("orders/shipping.json"):
            return {"standard": 399}
        if "orders.json?page" in url:
            return {"data": self.orders, "last_page": 1}
        if url.endswith("/orders.json") and method == "POST":
            return {"id": "new-1"}
        if "send_to_production" in url:
            return {}
        return {"status": self.status, "shipments": self.shipments}


PFY_ORDER = {"items": [{"supplier_product_id": "p1", "supplier_variant_id": 11, "quantity": 2}],
             "address_to": {"country": "US"}}


class PrintifySupplierTest(unittest.TestCase):
    def test_quote_uses_variant_cost_plus_shipping(self):
        q = PrintifySupplier("t", "s", http=FakePrintify()).quote(PFY_ORDER)
        self.assertEqual((q.available, q.total_cost), (True, 24.99))

    def test_disabled_variant_unavailable(self):
        order = {**PFY_ORDER, "items": [{"supplier_product_id": "p1", "supplier_variant_id": 12, "quantity": 1}]}
        q = PrintifySupplier("t", "s", http=FakePrintify()).quote(order)
        self.assertEqual((q.available, q.unavailable_skus), (False, [12]))

    def test_adopts_order_imported_from_etsy(self):
        api = FakePrintify(orders=[{"id": "imp-9", "metadata": {"shop_order_id": 555}}])
        sid = PrintifySupplier("t", "s", http=api).create_draft(PFY_ORDER, "555")
        self.assertEqual(sid, "imp-9")
        self.assertFalse(any(m == "POST" and u.endswith("/orders.json") for m, u, _ in api.calls))

    def test_creates_when_absent(self):
        api = FakePrintify()
        self.assertEqual(PrintifySupplier("t", "s", http=api).create_draft(PFY_ORDER, "556"), "new-1")
        body = next(b for m, u, b in api.calls if m == "POST" and u.endswith("/orders.json"))
        self.assertEqual(body["external_id"], "556")
        self.assertFalse(body["send_shipping_notification"])

    def test_status_mapping(self):
        sup = PrintifySupplier("t", "s", http=FakePrintify(status="fulfilled", shipments=[
            {"carrier": "usps", "number": "94001", "url": "u", "delivered_at": None}]))
        self.assertEqual(sup.get_status("1")["state"], "shipped")
        sup = PrintifySupplier("t", "s", http=FakePrintify(status="fulfilled", shipments=[
            {"carrier": "usps", "number": "94001", "url": "u", "delivered_at": "2026-10-20"}]))
        self.assertEqual(sup.get_status("1")["state"], "delivered")
        self.assertEqual(PrintifySupplier("t", "s", http=FakePrintify(status="on-hold")).get_status("1")["state"], "draft")

    def test_auto_approved_order_alerts_and_counts_spend(self):
        alerts = []
        store = OrderStore(sqlite3.connect(":memory:"))
        api = FakePrintify(status="in-production")
        p = FulfillmentPipeline(store, PrintifySupplier("t", "s", http=api), Settings(), alert=alerts.append)
        store.ingest("777", PFY_ORDER)
        p.run()
        self.assertEqual(store.get("777")["state"], "in_production")
        self.assertIn("without our approval", alerts[0])
        self.assertFalse(any("send_to_production" in u for _, u, _ in api.calls))


class EtsyTest(unittest.TestCase):
    RECEIPT = {"receipt_id": 4242, "name": "Ada Lovelace", "buyer_email": "ada@example.com",
               "first_line": "1 Main St", "city": "LA", "state": "CA", "zip": "90001", "country_iso": "US",
               "grandtotal": {"amount": 3499, "divisor": 100},
               "transactions": [{"sku": "TEE-M", "quantity": 1, "listing_id": 1},
                                {"sku": "", "quantity": 1, "listing_id": 2}]}

    def test_to_order_flags_unmapped(self):
        oid, payload = etsy.to_order(self.RECEIPT, {"TEE-M": {"supplier_product_id": "p", "supplier_variant_id": 1}},
                                     {"TEE-M": 16.0})
        self.assertEqual((oid, payload["total"], payload["expected_supplier_cost"]), ("4242", 34.99, 16.0))
        self.assertEqual(payload["unmapped_skus"], ["listing:2"])
        self.assertEqual(payload["address_to"]["last_name"], "Lovelace")
        store, sup, p = pipeline()
        store.ingest(oid, payload)
        p.run()
        self.assertEqual(store.get(oid)["state"], "needs_review")
        self.assertEqual(sup.drafts, {})

    def test_add_tracking_idempotent(self):
        calls = []

        def http(url, method="GET", body=None, headers=None):
            calls.append((method, url, headers))
            return {"shipments": [{"tracking_code": "T1"}]}

        client = etsy.EtsyClient("key:secret", "tok", "shop", http=http)
        self.assertFalse(client.add_tracking(1, "T1", "usps"))
        self.assertTrue(client.add_tracking(1, "T2", "usps"))
        self.assertEqual(calls[0][2]["x-api-key"], "key:secret")
        self.assertEqual(sum(1 for m, _, _ in calls if m == "POST"), 1)

    def test_receipts_paginate(self):
        pages = [{"count": 3, "results": [1, 2]}, {"count": 3, "results": [3]}]
        client = etsy.EtsyClient("k:s", "t", "shop", http=lambda *a, **k: pages.pop(0))
        self.assertEqual(client.unshipped_paid_receipts(limit=2), [1, 2, 3])


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

    def test_delivered_without_seeing_shipped(self):
        store, sup, p = pipeline(APPROVED)
        store.ingest("1001", ORDER)
        p.run()
        sup.status[store.get("1001")["supplier_order_id"]] = {"state": "delivered", "tracking_number": "T"}
        p.run()
        self.assertEqual(store.get("1001")["state"], "delivered")

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
        self.bot = SupportAssistant(Policy("Leaf & Line", "help@example.com", (2, 10), (3, 5), product_facts={"material": "100% cotton"}), self.store)

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
