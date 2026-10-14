"""Printify catalog access.

Public (no token): blueprints, a provider's variants, and its shipping
profiles, so shipping cost and handling time are LIVE.
Token required: the provider list per blueprint, and production costs (the
`cost` field on a shop product's variants). Until PRINTIFY_TOKEN is set, cost
comes from data/printify_costs.json (owner-entered, with source) or, failing
that, from a clearly labelled Printful benchmark for the same blank.
"""
import json
from collections import Counter
from pathlib import Path

from ..config import DATA_DIR
from ..util import get_logger, http_json
from . import printful_catalog as pf
from .printful_catalog import _cache_get, _cache_put

API = "https://api.printify.com/v1"
PRINTIFY_CHOICE = 99  # Printify's own routing provider
log = get_logger("dropship.printify")


def _get(path: str, max_age_s: int = 86400, token: str | None = None):
    name = "printify_" + path.strip("/").replace("/", "_")
    cached = _cache_get(name, max_age_s)
    if cached is not None:
        return cached
    headers = {"Authorization": f"Bearer {token}"} if token else None
    result = http_json(f"{API}/{path}", headers=headers)
    _cache_put(name, result)
    return result


def blueprint(blueprint_id: int) -> dict:
    return _get(f"catalog/blueprints/{blueprint_id}.json")


def variants(blueprint_id: int, provider_id: int = PRINTIFY_CHOICE) -> list[dict]:
    return _get(f"catalog/blueprints/{blueprint_id}/print_providers/{provider_id}/variants.json")["variants"]


def us_shipping(blueprint_id: int, provider_id: int = PRINTIFY_CHOICE) -> dict:
    """US first/additional item cost from the profile covering most variants."""
    data = _get(f"catalog/blueprints/{blueprint_id}/print_providers/{provider_id}/shipping.json")
    us = [p for p in data["profiles"] if "US" in p["countries"]]
    if not us:
        raise LookupError(f"blueprint {blueprint_id} provider {provider_id} does not ship to US")
    weights = Counter()
    for p in us:
        weights[(p["first_item"]["cost"], p["additional_items"]["cost"])] += len(p["variant_ids"])
    (first, additional), _ = weights.most_common(1)[0]
    worst = max(p["first_item"]["cost"] for p in us)
    return {
        "first_item": first / 100,
        "additional_item": additional / 100,
        "first_item_max": worst / 100,
        "handling_days_max": data["handling_time"]["value"],
        "source": f"printify catalog API, provider {provider_id} (live)",
    }


def load_manual_costs(path: Path | None = None) -> dict:
    """{"<blueprint_id>": {"cost": 9.5, "source": "...", "date": "YYYY-MM-DD"}}"""
    path = path or Path(DATA_DIR) / "printify_costs.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    for key, entry in data.items():
        for field in ("cost", "source", "date"):
            if field not in entry:
                raise ValueError(f"printify_costs.json[{key}] missing '{field}'")
    return data


def shop_product_costs(shop_id: str, product_id: str, token: str, http=http_json) -> dict:
    """Live production cost per variant (USD) from a product in your Printify shop."""
    res = http(f"{API}/shops/{shop_id}/products/{product_id}.json", headers={"Authorization": f"Bearer {token}"})
    return {v["id"]: v["cost"] / 100 for v in res["variants"] if v.get("is_enabled", True)}


def resolve_cost(blueprint_id: int, printful_benchmark_id: int | None, manual: dict,
                 printful_fetch=pf.get_product) -> tuple[float | None, str]:
    """Best available cost and its provenance label."""
    entry = manual.get(str(blueprint_id))
    if entry:
        return float(entry["cost"]), f"owner-entered: {entry['source']} ({entry['date']})"
    if printful_benchmark_id:
        summary = pf.variant_summary(printful_fetch(printful_benchmark_id))
        return summary["cost_median"], f"BENCHMARK: Printful live cost for same blank (#{printful_benchmark_id}), not a Printify price"
    return None, "unknown: needs PRINTIFY_TOKEN or owner entry"
