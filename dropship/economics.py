"""Unit economics. Every figure here is an ESTIMATE until replaced by realized data.

contribution = price + shipping_charged - product_cost - shipping_cost
               - payment_fees - channel_fees - expected_refund_cost - ad_cost_per_order

channel_fees covers marketplace charges (Etsy transaction, listing renewal and
the expected share of Offsite Ads fees).
"""
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class FeeModel:
    # Shopify Payments (Basic plan, US online card rate) as published; verify
    # against your plan before relying on it.
    payment_pct: float = 0.029
    payment_fixed: float = 0.30
    # Share of orders refunded; the refunded order loses product + shipping cost.
    refund_rate: float = 0.05
    # Marketplace fees, as a share of item price + shipping charged.
    transaction_pct: float = 0.0
    per_order_fixed: float = 0.0
    offsite_ads_pct: float = 0.0
    # Expected share of orders attributed to Offsite Ads (unknown until real data).
    offsite_ads_share: float = 0.0


SHOPIFY = FeeModel()
# Etsy US seller fees per third-party 2026 guides (etsy.com/legal/fees blocked
# automated fetch): 6.5% transaction on price + shipping, 3% + $0.25 Etsy
# Payments, $0.20 listing renewal per sale, Offsite Ads 15% of attributed
# orders under $10k/yr (opt-out allowed), 12% mandatory above. Verify in your
# shop's Payments settings.
ETSY = FeeModel(payment_pct=0.03, payment_fixed=0.25, transaction_pct=0.065,
                per_order_fixed=0.20, offsite_ads_pct=0.15)


@dataclass(frozen=True)
class UnitEconomics:
    price: float
    product_cost: float
    shipping_cost: float
    shipping_charged: float
    payment_fees: float
    channel_fees: float
    expected_refund_cost: float
    ad_cost_per_order: float
    contribution: float
    margin_pct: float
    breakeven_cpa: float
    label: str = "ESTIMATE"

    def as_dict(self) -> dict:
        return asdict(self)


def contribution(
    price: float,
    product_cost: float,
    shipping_cost: float,
    ad_cost_per_order: float = 0.0,
    shipping_charged: float = 0.0,
    fees: FeeModel = SHOPIFY,
) -> UnitEconomics:
    if price <= 0:
        raise ValueError("price must be positive")
    revenue = price + shipping_charged
    payment = revenue * fees.payment_pct + fees.payment_fixed
    channel = (revenue * (fees.transaction_pct + fees.offsite_ads_pct * fees.offsite_ads_share)
               + fees.per_order_fixed)
    refund_cost = fees.refund_rate * (product_cost + shipping_cost)
    before_ads = revenue - product_cost - shipping_cost - payment - channel - refund_cost
    contrib = before_ads - ad_cost_per_order
    return UnitEconomics(
        price=round(price, 2),
        product_cost=round(product_cost, 2),
        shipping_cost=round(shipping_cost, 2),
        shipping_charged=round(shipping_charged, 2),
        payment_fees=round(payment, 2),
        channel_fees=round(channel, 2),
        expected_refund_cost=round(refund_cost, 2),
        ad_cost_per_order=round(ad_cost_per_order, 2),
        contribution=round(contrib, 2),
        margin_pct=round(100 * contrib / revenue, 1),
        breakeven_cpa=round(before_ads, 2),
    )


def suggested_price(product_cost: float, shipping_cost: float, target_margin_before_ads: float = 0.45,
                    fees: FeeModel = SHOPIFY) -> float:
    """Smallest .99 price whose pre-ad margin meets the target (free shipping)."""
    price = 9.99
    while price < 500:
        econ = contribution(price, product_cost, shipping_cost, fees=fees)
        if econ.breakeven_cpa / price >= target_margin_before_ads:
            return price
        price += 1.0
    raise ValueError("no sensible price meets target margin")
