"""Order pipeline: store order -> supplier order -> tracking, with idempotency.

States:
  received -> needs_review | ready -> submitted_draft -> confirmed
           -> in_production -> shipped -> delivered
  any non-terminal -> failed | cancelled

Guarantees:
  * One supplier order per store order: the store order id is the primary key
    and the supplier external_id, so retries cannot create duplicates.
  * Nothing is confirmed (charged) with the supplier unless live orders are
    enabled AND the order fits the owner-approved spending limits.
  * Unavailable SKUs move the order to needs_review; no silent substitution.
"""
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from ..config import Settings
from ..util import get_logger

log = get_logger("dropship.orders")

TRANSITIONS = {
    "received": {"ready", "needs_review", "cancelled"},
    "needs_review": {"ready", "cancelled"},
    "ready": {"submitted_draft", "failed", "needs_review", "cancelled"},
    "submitted_draft": {"confirmed", "needs_review", "failed", "cancelled"},
    "confirmed": {"in_production", "shipped", "failed", "cancelled"},
    "in_production": {"shipped", "failed"},
    "shipped": {"delivered"},
    "failed": {"ready", "needs_review"},
    "delivered": set(),
    "cancelled": set(),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    store_order_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    payload TEXT NOT NULL,
    supplier_order_id TEXT,
    supplier_cost REAL,
    tracking_number TEXT,
    tracking_url TEXT,
    carrier TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS order_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_order_id TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    detail TEXT,
    at TEXT NOT NULL
);
"""


class InvalidTransition(Exception):
    pass


@dataclass
class SupplierQuote:
    total_cost: float
    available: bool
    unavailable_skus: list


class Supplier(Protocol):
    def quote(self, order: dict) -> SupplierQuote: ...
    def create_draft(self, order: dict, external_id: str) -> str: ...
    def confirm(self, supplier_order_id: str) -> None: ...
    def get_status(self, supplier_order_id: str) -> dict: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class OrderStore:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)

    def ingest(self, store_order_id: str, payload: dict) -> bool:
        """Insert once. Returns False for a duplicate webhook/poll delivery."""
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO orders (store_order_id, state, payload, created_at, updated_at)"
            " VALUES (?, 'received', ?, ?, ?)",
            (store_order_id, json.dumps(payload), _now(), _now()),
        )
        self.conn.commit()
        if cur.rowcount:
            self._event(store_order_id, None, "received", "ingested")
        return bool(cur.rowcount)

    def get(self, store_order_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM orders WHERE store_order_id=?", (store_order_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["payload"] = json.loads(d["payload"])
        return d

    def by_state(self, *states: str) -> list[dict]:
        marks = ",".join("?" for _ in states)
        rows = self.conn.execute(f"SELECT store_order_id FROM orders WHERE state IN ({marks})", states)
        return [self.get(r["store_order_id"]) for r in rows]

    def transition(self, store_order_id: str, to_state: str, detail: str = "", **fields) -> None:
        order = self.get(store_order_id)
        if order is None:
            raise KeyError(store_order_id)
        if to_state not in TRANSITIONS[order["state"]]:
            raise InvalidTransition(f"{store_order_id}: {order['state']} -> {to_state}")
        sets = ", ".join(f"{k}=?" for k in ["state", "updated_at", *fields])
        # Compare-and-set on the old state guards against concurrent workers.
        cur = self.conn.execute(
            f"UPDATE orders SET {sets} WHERE store_order_id=? AND state=?",
            (to_state, _now(), *fields.values(), store_order_id, order["state"]),
        )
        self.conn.commit()
        if not cur.rowcount:
            raise InvalidTransition(f"{store_order_id}: state changed concurrently")
        self._event(store_order_id, order["state"], to_state, detail)

    def spent_today(self) -> float:
        today = datetime.now(timezone.utc).date().isoformat()
        row = self.conn.execute(
            "SELECT COALESCE(SUM(o.supplier_cost),0) FROM orders o JOIN order_events e"
            " ON e.store_order_id=o.store_order_id WHERE e.to_state='confirmed' AND e.at LIKE ?",
            (today + "%",),
        ).fetchone()
        return float(row[0])

    def _event(self, oid, frm, to, detail):
        self.conn.execute(
            "INSERT INTO order_events (store_order_id, from_state, to_state, detail, at) VALUES (?,?,?,?,?)",
            (oid, frm, to, detail, _now()),
        )
        self.conn.commit()


class FulfillmentPipeline:
    def __init__(self, store: OrderStore, supplier: Supplier, settings: Settings, alert=None):
        self.store = store
        self.supplier = supplier
        self.settings = settings
        self.alert = alert or (lambda msg: log.warning(f"ALERT: {msg}"))

    def validate(self, oid: str) -> None:
        order = self.store.get(oid)
        unmapped = order["payload"].get("unmapped_skus")
        if unmapped:
            self.store.transition(oid, "needs_review", f"unmapped SKUs: {unmapped}")
            self.alert(f"Order {oid} needs review: no supplier mapping for {unmapped}")
            return
        quote = self.supplier.quote(order["payload"])
        if not quote.available:
            self.store.transition(oid, "needs_review", f"unavailable: {quote.unavailable_skus}")
            self.alert(f"Order {oid} needs review: supplier SKUs unavailable {quote.unavailable_skus}")
            return
        expected = order["payload"].get("expected_supplier_cost")
        if expected and quote.total_cost > expected * 1.10:
            self.store.transition(oid, "needs_review", f"cost {quote.total_cost} > expected {expected}")
            self.alert(f"Order {oid}: supplier cost {quote.total_cost:.2f} exceeds expected {expected:.2f} by >10%")
            return
        self.store.transition(oid, "ready", "validated", supplier_cost=quote.total_cost)

    def submit_draft(self, oid: str) -> None:
        order = self.store.get(oid)
        if order["supplier_order_id"]:
            return  # already created; idempotent
        try:
            sid = self.supplier.create_draft(order["payload"], external_id=oid)
        except Exception as exc:  # noqa: BLE001 - recorded and alerted
            self.store.transition(oid, "failed", "draft failed", last_error=str(exc)[:500])
            self.alert(f"Order {oid}: supplier draft failed: {exc}")
            return
        self.store.transition(oid, "submitted_draft", "draft created", supplier_order_id=sid)

    def confirm(self, oid: str) -> bool:
        """Charge the supplier. Gated by live flag and spending limits."""
        order = self.store.get(oid)
        limits = self.settings.limits
        cost = order["supplier_cost"] or 0.0
        # The supplier may have started production on its own (e.g. Printify
        # auto-approval of imported Etsy orders). Record the spend and alert.
        external = self.supplier.get_status(order["supplier_order_id"]).get("state")
        if external in ("failed", "cancelled", "canceled"):
            self.store.transition(oid, "needs_review", f"supplier draft is {external}")
            self.alert(f"Order {oid}: supplier draft is '{external}'")
            return False
        if external in ("in_production", "inprocess", "shipped", "delivered", "fulfilled"):
            self.store.transition(oid, "confirmed", f"confirmed OUTSIDE spending gates (supplier: {external})")
            self.alert(f"Order {oid} was sent to production by the supplier without our approval; "
                       "check that supplier order approval is set to manual")
            return False
        reasons = []
        if not self.settings.live_orders_enabled:
            reasons.append("live orders disabled")
        if cost > limits.max_supplier_order_usd:
            reasons.append(f"cost {cost:.2f} > per-order limit {limits.max_supplier_order_usd:.2f}")
        if self.store.spent_today() + cost > limits.max_daily_supplier_spend_usd:
            reasons.append("daily supplier spend limit reached")
        if reasons:
            log.info(f"order {oid} held as draft: {'; '.join(reasons)}")
            return False
        self.supplier.confirm(order["supplier_order_id"])
        self.store.transition(oid, "confirmed", f"confirmed at cost {cost:.2f}")
        return True

    def sync_tracking(self, oid: str) -> None:
        order = self.store.get(oid)
        status = self.supplier.get_status(order["supplier_order_id"])
        state = status.get("state")
        if state in ("shipped", "delivered") and order["state"] in ("confirmed", "in_production"):
            self.store.transition(
                oid, "shipped", "tracking received",
                tracking_number=status.get("tracking_number"),
                tracking_url=status.get("tracking_url"),
                carrier=status.get("carrier"),
            )
            order = self.store.get(oid)
        elif state == "in_production" and order["state"] == "confirmed":
            self.store.transition(oid, "in_production", "supplier in production")
        if state == "delivered" and order["state"] == "shipped":
            self.store.transition(oid, "delivered", "carrier delivered")
        elif state in ("failed", "canceled", "cancelled", "onhold"):
            if order["state"] not in ("failed", "cancelled", "needs_review"):
                self.alert(f"Order {oid}: supplier reports '{state}'")

    def run(self) -> dict:
        counts = {"validated": 0, "drafted": 0, "confirmed": 0, "held": 0}
        for o in self.store.by_state("received"):
            self.validate(o["store_order_id"])
            counts["validated"] += 1
        for o in self.store.by_state("ready"):
            self.submit_draft(o["store_order_id"])
            counts["drafted"] += 1
        for o in self.store.by_state("submitted_draft"):
            if self.confirm(o["store_order_id"]):
                counts["confirmed"] += 1
            else:
                counts["held"] += 1
        for o in self.store.by_state("confirmed", "in_production", "shipped"):
            self.sync_tracking(o["store_order_id"])
        return counts
