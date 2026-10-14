"""Printful catalog access.

The v1 /products endpoints are public (no token) and return live production
prices and stock. Shipping rates need an authenticated token, so until one is
connected we fall back to Printful's PUBLISHED US flat rates below, which are
flagged as such in every result.
"""
import json
import time
from pathlib import Path

from ..config import DATA_DIR
from ..util import get_logger, http_json

API = "https://api.printful.com"
log = get_logger("dropship.printful")

# Source: https://www.printful.com/shipping, fetched 2026-10-14. US Standard
# flat rate (first item, each additional). Re-verify with the shipping/rates
# API once PRINTFUL_TOKEN is configured.
PUBLISHED_US_SHIPPING = {
    "T-SHIRT": (4.95, 2.20),
    "HOODIE": (8.79, 2.50),
    "MUG": (6.69, 3.50),
    "POSTER": (4.99, 0.40),
    "FRAMED-POSTER": (10.89, 4.50),
    "STICKER": (4.49, 0.05),
    "TOTE": (4.69, 2.00),
    "PHONE-CASE": (5.19, 1.00),
    "HAT": (4.69, 2.00),
    "CANVAS": (6.79, 6.49),
    "BLANKET": (8.29, 2.50),
    "CANDLE": (8.29, 3.49),
}
SHIPPING_SOURCE = "printful.com/shipping published US flat rate (2026-10-14), not API-verified"
PUBLISHED_FULFILLMENT_DAYS = (2, 5)
PUBLISHED_US_TRANSIT_DAYS = (3, 4)


def _cache_get(name: str, max_age_s: int):
    path = Path(DATA_DIR) / "cache" / name
    if path.exists() and time.time() - path.stat().st_mtime < max_age_s:
        return json.loads(path.read_text())
    return None


def _cache_put(name: str, payload) -> None:
    path = Path(DATA_DIR) / "cache" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def list_products(max_age_s: int = 86400) -> list[dict]:
    cached = _cache_get("printful_products.json", max_age_s)
    if cached is not None:
        return cached
    result = http_json(f"{API}/products")["result"]
    _cache_put("printful_products.json", result)
    return result


def get_product(product_id: int, max_age_s: int = 86400) -> dict:
    name = f"printful_product_{product_id}.json"
    cached = _cache_get(name, max_age_s)
    if cached is not None:
        return cached
    result = http_json(f"{API}/products/{product_id}")["result"]
    _cache_put(name, result)
    return result


def variant_summary(product: dict, region: str = "US") -> dict:
    """Live cost range and stock status for variants sold in `region`."""
    variants = product["variants"]
    regional = [v for v in variants if not v.get("availability_regions") or region in v["availability_regions"]]
    in_stock = [v for v in regional if v.get("in_stock")]
    prices = sorted(float(v["price"]) for v in in_stock) or [0.0]
    return {
        "variants_total": len(variants),
        "variants_in_stock": len(in_stock),
        "cost_min": prices[0],
        "cost_median": prices[len(prices) // 2],
        "cost_max": prices[-1],
    }
