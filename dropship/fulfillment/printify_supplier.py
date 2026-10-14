"""Printify supplier adapter (REST API v1).

With Printify's native Etsy integration, Etsy orders are imported into
Printify automatically. To keep our spending gates in control, the Printify
store's order approval must be set to MANUAL; otherwise Printify sends
imported orders to production (and charges) on its own.

Duplicate prevention: create_draft() first adopts an order Printify already
imported for the same Etsy receipt (metadata.shop_order_id) or created with
the same external_id, and only creates a new order if none exists.
"""
from ..util import http_json
from .orders import SupplierQuote

API = "https://api.printify.com/v1"

STATUS_MAP = {
    "pending": "draft",
    "on-hold": "draft",
    "cost-calculation": "draft",
    "sending-to-production": "in_production",
    "sending_to_production_delegate": "in_production",
    "sending_to_production_delegate_sync": "in_production",
    "in-production": "in_production",
    "partially-fulfilled": "shipped",
    "fulfilled": "shipped",
    "canceled": "cancelled",
    "has-issues": "failed",
    "unfulfillable": "failed",
    "payment-not-received": "failed",
    "source-check-failed": "failed",
}


class PrintifySupplier:
    def __init__(self, token: str, shop_id: str, http=http_json, scan_pages: int = 10):
        if not token or not shop_id:
            raise ValueError("PRINTIFY_TOKEN and PRINTIFY_SHOP_ID are required")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._shop = f"{API}/shops/{shop_id}"
        self._http = http
        self._scan_pages = scan_pages
        self._product_cache: dict[str, dict] = {}

    def _get(self, url):
        return self._http(url, "GET", None, self._headers)

    def _post(self, url, body):
        return self._http(url, "POST", body, self._headers)

    def _line_items(self, order: dict) -> list[dict]:
        return [{"product_id": i["supplier_product_id"], "variant_id": i["supplier_variant_id"],
                 "quantity": i["quantity"]} for i in order["items"]]

    def _variant(self, product_id: str, variant_id: int) -> dict | None:
        if product_id not in self._product_cache:
            self._product_cache[product_id] = self._get(f"{self._shop}/products/{product_id}.json")
        return next((v for v in self._product_cache[product_id]["variants"] if v["id"] == variant_id), None)

    def quote(self, order: dict) -> SupplierQuote:
        total, unavailable = 0.0, []
        for item in order["items"]:
            v = self._variant(item["supplier_product_id"], item["supplier_variant_id"])
            if not v or not v.get("is_enabled", True) or v.get("is_available") is False:
                unavailable.append(item["supplier_variant_id"])
                continue
            total += v["cost"] / 100 * item["quantity"]
        if unavailable:
            return SupplierQuote(total, False, unavailable)
        ship = self._post(f"{self._shop}/orders/shipping.json",
                          {"line_items": self._line_items(order), "address_to": order["address_to"]})
        return SupplierQuote(round(total + ship["standard"] / 100, 2), True, [])

    def find_existing(self, external_id: str) -> str | None:
        for page in range(1, self._scan_pages + 1):
            res = self._get(f"{self._shop}/orders.json?page={page}&limit=10")
            for o in res.get("data", []):
                meta = o.get("metadata") or {}
                if str(meta.get("shop_order_id")) == external_id or o.get("external_id") == external_id:
                    return str(o["id"])
            if page >= res.get("last_page", page):
                break
        return None

    def create_draft(self, order: dict, external_id: str) -> str:
        existing = self.find_existing(external_id)
        if existing:
            return existing
        body = {
            "external_id": external_id,
            "line_items": self._line_items(order),
            "shipping_method": 1,  # standard
            "send_shipping_notification": False,  # Etsy notifies the buyer
            "address_to": order["address_to"],
        }
        return str(self._post(f"{self._shop}/orders.json", body)["id"])

    def confirm(self, supplier_order_id: str) -> None:
        self._post(f"{self._shop}/orders/{supplier_order_id}/send_to_production.json", {})

    def get_status(self, supplier_order_id: str) -> dict:
        o = self._get(f"{self._shop}/orders/{supplier_order_id}.json")
        state = STATUS_MAP.get(o["status"], o["status"])
        out = {"state": state, "raw_status": o["status"]}
        shipments = o.get("shipments") or []
        if shipments:
            s = shipments[-1]
            out.update(tracking_number=s.get("number"), tracking_url=s.get("url"), carrier=s.get("carrier"))
            if state == "shipped" and all(x.get("delivered_at") for x in shipments):
                out["state"] = "delivered"
        return out
