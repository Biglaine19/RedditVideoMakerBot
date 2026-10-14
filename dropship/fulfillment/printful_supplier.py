"""Printful supplier adapter (Orders API v1) and an offline simulator.

Drafts are created with confirm=false, so Printful does not charge until
confirm() is called, which the pipeline only does within approved limits.
Printful rejects a second order with the same external_id, which backs up
our local idempotency.
"""
from ..util import http_json
from .orders import SupplierQuote

API = "https://api.printful.com"


class PrintfulSupplier:
    def __init__(self, token: str, store_id: str | None = None, http=http_json):
        if not token:
            raise ValueError("PRINTFUL_TOKEN is required")
        self._headers = {"Authorization": f"Bearer {token}"}
        if store_id:
            self._headers["X-PF-Store-Id"] = store_id
        self._http = http

    def _items(self, order: dict) -> list[dict]:
        return [{"sync_variant_id": i["supplier_variant_id"], "quantity": i["quantity"]} for i in order["items"]]

    def quote(self, order: dict) -> SupplierQuote:
        body = {"recipient": order["recipient"], "items": self._items(order)}
        res = self._http(f"{API}/orders/estimate-costs", "POST", body, self._headers)["result"]
        return SupplierQuote(total_cost=float(res["costs"]["total"]), available=True, unavailable_skus=[])

    def create_draft(self, order: dict, external_id: str) -> str:
        body = {"external_id": external_id, "recipient": order["recipient"], "items": self._items(order)}
        res = self._http(f"{API}/orders?confirm=false", "POST", body, self._headers)["result"]
        return str(res["id"])

    def confirm(self, supplier_order_id: str) -> None:
        self._http(f"{API}/orders/{supplier_order_id}/confirm", "POST", {}, self._headers)

    def get_status(self, supplier_order_id: str) -> dict:
        res = self._http(f"{API}/orders/{supplier_order_id}", "GET", None, self._headers)["result"]
        shipments = res.get("shipments") or []
        state = {"fulfilled": "shipped", "partial": "shipped", "inprocess": "in_production"}.get(res["status"], res["status"])
        out = {"state": state}
        if shipments:
            s = shipments[-1]
            out.update(tracking_number=s.get("tracking_number"), tracking_url=s.get("tracking_url"), carrier=s.get("carrier"))
        return out


class SimulatedSupplier:
    """Deterministic offline supplier for end-to-end tests. Never charges."""

    def __init__(self, unit_cost: float = 12.0, unavailable: set | None = None):
        self.unit_cost = unit_cost
        self.unavailable = unavailable or set()
        self.drafts: dict[str, dict] = {}
        self.external_ids: set[str] = set()
        self.status: dict[str, dict] = {}
        self.confirm_calls = 0

    def quote(self, order):
        bad = [i["supplier_variant_id"] for i in order["items"] if i["supplier_variant_id"] in self.unavailable]
        total = sum(self.unit_cost * i["quantity"] for i in order["items"])
        return SupplierQuote(total_cost=total, available=not bad, unavailable_skus=bad)

    def create_draft(self, order, external_id):
        if external_id in self.external_ids:
            raise RuntimeError(f"duplicate external_id {external_id}")
        self.external_ids.add(external_id)
        sid = f"SIM-{len(self.drafts) + 1}"
        self.drafts[sid] = order
        self.status[sid] = {"state": "draft"}
        return sid

    def confirm(self, sid):
        self.confirm_calls += 1
        self.status[sid] = {"state": "pending"}

    def get_status(self, sid):
        return self.status[sid]
