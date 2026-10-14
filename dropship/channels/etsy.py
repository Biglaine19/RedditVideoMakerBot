"""Etsy Open API v3 client: read paid receipts, map them to supplier items,
and (optionally) post tracking.

Auth: `x-api-key: <keystring>:<shared secret>` plus an OAuth 2.0 bearer
token with scopes transactions_r and transactions_w (verify scopes per
endpoint in Etsy's API reference). Values come from the environment only.

If Printify's native Etsy integration is connected, Printify already pushes
tracking to Etsy; leave add_tracking unused in that setup to avoid double
updates (it is idempotent anyway).
"""
from ..util import http_json

API = "https://openapi.etsy.com/v3/application"


class EtsyClient:
    def __init__(self, api_key: str, oauth_token: str, shop_id: str, http=http_json):
        if not (api_key and oauth_token and shop_id):
            raise ValueError("ETSY_API_KEY, ETSY_OAUTH_TOKEN and ETSY_SHOP_ID are required")
        self._headers = {"x-api-key": api_key, "Authorization": f"Bearer {oauth_token}"}
        self._shop = f"{API}/shops/{shop_id}"
        self._http = http

    def unshipped_paid_receipts(self, limit: int = 100) -> list[dict]:
        out, offset = [], 0
        while True:
            res = self._http(f"{self._shop}/receipts?was_paid=true&was_shipped=false&limit={limit}&offset={offset}",
                             "GET", None, self._headers)
            out += res.get("results", [])
            offset += limit
            if offset >= res.get("count", 0):
                return out

    def receipt(self, receipt_id: int) -> dict:
        return self._http(f"{self._shop}/receipts/{receipt_id}", "GET", None, self._headers)

    def add_tracking(self, receipt_id: int, tracking_code: str, carrier_name: str) -> bool:
        """Returns False if this tracking code is already on the receipt."""
        existing = self.receipt(receipt_id).get("shipments") or []
        if any(s.get("tracking_code") == tracking_code for s in existing):
            return False
        self._http(f"{self._shop}/receipts/{receipt_id}/tracking", "POST",
                   {"tracking_code": tracking_code, "carrier_name": carrier_name}, self._headers)
        return True


def to_order(receipt: dict, sku_map: dict, expected_cost: dict | None = None) -> tuple[str, dict]:
    """Convert an Etsy receipt to the pipeline's order payload.

    sku_map: Etsy SKU -> {"supplier_product_id": ..., "supplier_variant_id": ...}
    expected_cost: Etsy SKU -> expected supplier cost incl. shipping share (optional)
    Unmapped SKUs are listed so the order goes to manual review.
    """
    items, unmapped, expected = [], [], 0.0
    for t in receipt.get("transactions", []):
        sku = t.get("sku") or ""
        mapping = sku_map.get(sku)
        if not mapping:
            unmapped.append(sku or f"listing:{t.get('listing_id')}")
            continue
        items.append({**mapping, "quantity": t.get("quantity", 1), "sku": sku})
        if expected_cost and sku in expected_cost:
            expected += expected_cost[sku] * t.get("quantity", 1)
    name = (receipt.get("name") or "").strip()
    first, _, last = name.partition(" ")
    payload = {
        "channel": "etsy",
        "email": receipt.get("buyer_email") or "",
        "items": items,
        "unmapped_skus": unmapped,
        "address_to": {
            "first_name": first, "last_name": last,
            "email": receipt.get("buyer_email") or "",
            "country": receipt.get("country_iso"), "region": receipt.get("state") or "",
            "address1": receipt.get("first_line"), "address2": receipt.get("second_line") or "",
            "city": receipt.get("city"), "zip": receipt.get("zip"),
        },
        "total": _money(receipt.get("grandtotal")),
    }
    if expected:
        payload["expected_supplier_cost"] = round(expected, 2)
    return str(receipt["receipt_id"]), payload


def _money(m: dict | None) -> float | None:
    if not m:
        return None
    return m["amount"] / m["divisor"]
