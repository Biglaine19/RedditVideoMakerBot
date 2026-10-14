"""Customer support triage and grounded replies.

Answers come only from the order database and the written store policy.
Anything sensitive is escalated to the owner instead of answered:
complaints, chargebacks/disputes, legal or safety issues, and refunds
outside the approved policy.
"""
import re
from dataclasses import dataclass, field

from ..fulfillment.orders import OrderStore

ESCALATE_PATTERNS = {
    "payment_dispute": r"\b(chargeback|dispute|charged twice|double charged|fraud|unauthori[sz]ed)\b",
    "legal": r"\b(lawyer|attorney|sue|lawsuit|legal action|small claims|trading standards|bbb)\b",
    "safety": r"\b(injur\w*|burn(ed|t)?|allerg\w*|rash|fire|toxic|unsafe|hurt|choking)\b",
    "complaint": r"\b(scam|terrible|worst|disgusted|furious|unacceptable|never again)\b",
}
# Checked in order: a damaged delivery is a refund question, not a status one.
INTENT_PATTERNS = {
    "refund": r"\b(refund|return|money back|exchange|wrong size|damaged|defect\w*|misprint)\b",
    "order_status": r"\b(where is|status|track(ing)?|shipped|arrive|delivery|delivered|when will)\b",
    "shipping_info": r"\b(how long|ship(ping)? (time|cost)|ship to|international)\b",
    "product_info": r"\b(size|sizing|material|fabric|wash|care|dimensions|fit)\b",
}
ORDER_ID_RE = re.compile(r"#?\b(\d{4,})\b")


@dataclass
class Policy:
    store_name: str
    support_email: str
    # Must match the supplier and the processing time shown on the listing
    # (Printify Choice publishes handling of up to 10 days; Printful 2-5).
    fulfillment_days: tuple
    transit_days_us: tuple
    return_window_days: int = 30
    # Print-on-demand items are made to order: damaged/misprinted items are
    # replaced or refunded; change-of-mind returns are not accepted.
    covers_change_of_mind: bool = False
    product_facts: dict = field(default_factory=dict)


@dataclass
class Reply:
    intent: str
    escalate: bool
    reasons: list
    text: str


class SupportAssistant:
    def __init__(self, policy: Policy, orders: OrderStore):
        self.policy = policy
        self.orders = orders

    def classify(self, message: str) -> tuple[str, list]:
        low = message.lower()
        reasons = [k for k, p in ESCALATE_PATTERNS.items() if re.search(p, low)]
        intent = next((k for k, p in INTENT_PATTERNS.items() if re.search(p, low)), "other")
        return intent, reasons

    def respond(self, message: str, customer_email: str | None = None) -> Reply:
        intent, reasons = self.classify(message)
        p = self.policy
        if reasons:
            return Reply(intent, True, reasons,
                         f"Thanks for reaching out. I've passed your message to the {p.store_name} owner, "
                         f"who will reply personally from {p.support_email}.")
        if intent == "order_status":
            return self._order_status(message, customer_email)
        if intent == "refund":
            text = (f"Every item is printed to order. If your item arrived damaged, defective or misprinted, "
                    f"reply within {p.return_window_days} days of delivery with your order number and a photo, "
                    f"and we'll arrange a replacement or refund.")
            if not p.covers_change_of_mind:
                text += " Because items are made to order, we can't accept returns for change of mind or wrong size ordered."
            # A refund decision itself is always made by a human in this version.
            return Reply(intent, True, ["refund_request"], text + " A team member will review your request.")
        if intent == "shipping_info":
            f, t = p.fulfillment_days, p.transit_days_us
            return Reply(intent, False, [],
                         f"Orders take {f[0]}-{f[1]} business days to produce, then US standard shipping usually "
                         f"takes {t[0]}-{t[1]} business days. These are estimates, not guarantees; "
                         f"you'll get a tracking link as soon as your order ships.")
        if intent == "product_info":
            facts = "; ".join(f"{k}: {v}" for k, v in p.product_facts.items())
            if facts:
                return Reply(intent, False, [], f"Here are the product details: {facts}.")
            return Reply(intent, True, ["missing_product_facts"], "Let me check that detail and get back to you.")
        return Reply(intent, True, ["unrecognised"], f"Thanks for your message. We'll reply from {p.support_email} shortly.")

    def _order_status(self, message: str, customer_email: str | None) -> Reply:
        m = ORDER_ID_RE.search(message)
        if not m:
            return Reply("order_status", False, [], "Could you share your order number (e.g. #1001) so I can look it up?")
        order = self.orders.get(m.group(1))
        # Only disclose order details to the email on the order.
        if not order or (customer_email or "").lower() != order["payload"].get("email", "").lower():
            return Reply("order_status", False, [],
                         "I couldn't match that order number to your email address. Please reply from the email "
                         "you used at checkout, or double-check the order number.")
        state = order["state"]
        if state == "shipped" and order["tracking_number"]:
            text = (f"Your order shipped via {order['carrier'] or 'the carrier'}. Tracking number "
                    f"{order['tracking_number']}" + (f": {order['tracking_url']}" if order["tracking_url"] else "."))
        elif state == "delivered":
            text = "The carrier marked your order as delivered. If you can't find it, let us know and we'll help."
        elif state in ("received", "ready", "submitted_draft", "confirmed", "in_production"):
            text = "Your order is being produced. You'll get a tracking link by email as soon as it ships."
        else:
            return Reply("order_status", True, [f"order_state_{state}"],
                         "I'm checking on your order with our team and will update you shortly.")
        return Reply("order_status", False, [], text)
