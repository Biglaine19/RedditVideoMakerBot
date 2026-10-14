"""Daily / weekly business summary.

Each metric carries a source label. When a data source is not connected the
metric is reported as UNAVAILABLE rather than zero or a guess.
"""
from dataclasses import dataclass

UNAVAILABLE = "UNAVAILABLE"


@dataclass
class Sources:
    store_orders: list | None = None  # from Shopify: [{id, total, refunded, created_at}]
    sessions: int | None = None  # from Shopify analytics
    ad_spend: float | None = None  # from ad platform APIs
    payment_fee_pct: float = 0.029
    payment_fee_fixed: float = 0.30
    chargebacks: float | None = None


def build(sources: Sources, order_store=None, support_open: int | None = None,
          integration_errors: list | None = None, period: str = "daily") -> dict:
    report = {"period": period, "metrics": {}, "notes": []}
    m = report["metrics"]

    def put(name, value, source):
        m[name] = {"value": value if value is not None else UNAVAILABLE, "source": source if value is not None else "not connected"}

    orders = sources.store_orders
    revenue = sum(o["total"] for o in orders) if orders is not None else None
    refunds = sum(o.get("refunded", 0) for o in orders) if orders is not None else None
    count = len(orders) if orders is not None else None
    put("total_sales", revenue, "shopify")
    put("orders", count, "shopify")
    put("refunds", refunds, "shopify")
    put("chargebacks", sources.chargebacks, "shopify_payments")
    put("average_order_value", round(revenue / count, 2) if count else (0.0 if count == 0 else None), "derived")
    fees = round(sum(o["total"] * sources.payment_fee_pct + sources.payment_fee_fixed for o in orders), 2) if orders is not None else None
    put("payment_fees_est", fees, "estimated from fee model")
    put("conversion_rate_pct", round(100 * count / sources.sessions, 2) if count is not None and sources.sessions else None, "derived")
    put("ad_spend", sources.ad_spend, "ad platforms")

    supplier_cost = None
    if order_store is not None:
        rows = order_store.conn.execute(
            "SELECT state, COUNT(*) n, COALESCE(SUM(supplier_cost),0) c FROM orders GROUP BY state"
        ).fetchall()
        m["fulfillment_status"] = {"value": {r["state"]: r["n"] for r in rows}, "source": "local order db"}
        supplier_cost = sum(r["c"] for r in rows if r["state"] in ("confirmed", "in_production", "shipped", "delivered"))
        m["needs_review"] = {"value": sum(r["n"] for r in rows if r["state"] in ("needs_review", "failed")), "source": "local order db"}
    put("supplier_costs_incl_shipping", supplier_cost, "local order db (supplier quotes)")

    parts = [revenue, refunds, fees, supplier_cost, sources.ad_spend]
    if all(p is not None for p in parts):
        profit = round(revenue - refunds - fees - supplier_cost - sources.ad_spend - (sources.chargebacks or 0), 2)
        put("profit", profit, "realized revenue minus estimated fees" if fees else "realized")
        report["notes"].append("Profit uses ESTIMATED payment fees until payout data is connected.")
    else:
        put("profit", None, "")
        report["notes"].append("Profit not computed: at least one input source is not connected.")

    put("open_support_tickets", support_open, "support queue")
    m["integration_failures"] = {"value": integration_errors or [], "source": "pipeline logs"}
    return report


def render_text(report: dict) -> str:
    lines = [f"== {report['period'].title()} business summary =="]
    for name, d in report["metrics"].items():
        lines.append(f"{name:32} {d['value']!s:>20}   [{d['source']}]")
    lines += [f"note: {n}" for n in report["notes"]]
    return "\n".join(lines)
