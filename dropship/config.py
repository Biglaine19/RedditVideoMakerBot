"""Runtime configuration, read from environment variables only.

Secrets (API tokens) are never written to disk or logs by this package.
Spending limits default to zero, which blocks every paid action until the
owner sets them explicitly.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(os.environ.get("DROPSHIP_DATA_DIR", Path(__file__).parent / "data"))


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class SpendingLimits:
    """Owner-approved limits. All zero = no live spending allowed."""

    max_supplier_order_usd: float = 0.0
    max_daily_supplier_spend_usd: float = 0.0
    max_daily_ad_spend_usd: float = 0.0
    max_auto_refund_usd: float = 0.0
    max_total_loss_usd: float = 0.0

    @classmethod
    def from_env(cls) -> "SpendingLimits":
        return cls(
            max_supplier_order_usd=_float("DROPSHIP_MAX_SUPPLIER_ORDER_USD", 0.0),
            max_daily_supplier_spend_usd=_float("DROPSHIP_MAX_DAILY_SUPPLIER_SPEND_USD", 0.0),
            max_daily_ad_spend_usd=_float("DROPSHIP_MAX_DAILY_AD_SPEND_USD", 0.0),
            max_auto_refund_usd=_float("DROPSHIP_MAX_AUTO_REFUND_USD", 0.0),
            max_total_loss_usd=_float("DROPSHIP_MAX_TOTAL_LOSS_USD", 0.0),
        )


@dataclass(frozen=True)
class Settings:
    live_orders_enabled: bool = False
    printful_token: str | None = field(default=None, repr=False)
    shopify_store: str | None = None
    shopify_token: str | None = field(default=None, repr=False)
    limits: SpendingLimits = SpendingLimits()

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            live_orders_enabled=os.environ.get("DROPSHIP_LIVE_ORDERS") == "1",
            printful_token=os.environ.get("PRINTFUL_TOKEN") or None,
            shopify_store=os.environ.get("SHOPIFY_STORE") or None,
            shopify_token=os.environ.get("SHOPIFY_ADMIN_TOKEN") or None,
            limits=SpendingLimits.from_env(),
        )
