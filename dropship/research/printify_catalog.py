"""Printify catalog access.

Blueprints, a provider's variants and its shipping profiles give LIVE shipping
cost and handling time. Unauthenticated catalog calls now return 401 for some
blueprints (seen 2026-10-15), so PRINTIFY_TOKEN from the environment is sent
when set. Token required: the provider list per blueprint, and production costs
(the `cost` field on a shop product's variants). Until PRINTIFY_TOKEN is set, cost
comes from data/printify_costs.json (owner-entered, with source) or, failing
that, from a clearly labelled Printful benchmark for the same blank.
"""
import json
import os
import time
from collections import Counter
from pathlib import Path

from ..config import DATA_DIR
from ..util import get_logger, http_json
from . import printful_catalog as pf
from .printful_catalog import _cache_get, _cache_put

API = "https://api.printify.com/v1"
PRINTIFY_CHOICE = 99  # Printify's own routing provider
CATALOG_MIN_INTERVAL_S = 0.65  # catalog limit is 100 req/min; stay under it
log = get_logger("dropship.printify")
_last_call = [0.0]


def _throttle(sleep=time.sleep, clock=time.monotonic) -> None:
    wait = _last_call[0] + CATALOG_MIN_INTERVAL_S - clock()
    if wait > 0:
        sleep(wait)
    _last_call[0] = clock()


def _get(path: str, max_age_s: int = 86400, token: str | None = None):
    # Cache file names never include the token; responses hold catalog/shop data only.
    name = "printify_" + path.strip("/").replace("/", "_")
    cached = _cache_get(name, max_age_s)
    if cached is not None:
        return cached
    _throttle()
    token = token or os.environ.get("PRINTIFY_TOKEN") or None
    headers = {"Authorization": f"Bearer {token}"} if token else None
    result = http_json(f"{API}/{path}", headers=headers)
    _cache_put(name, result)
    return result


def blueprint(blueprint_id: int) -> dict:
    return _get(f"catalog/blueprints/{blueprint_id}.json")


def variants(blueprint_id: int, provider_id: int = PRINTIFY_CHOICE) -> list[dict]:
    return _get(f"catalog/blueprints/{blueprint_id}/print_providers/{provider_id}/variants.json")["variants"]


def us_shipping(blueprint_id: int, provider_id: int = PRINTIFY_CHOICE, token: str | None = None) -> dict:
    """US first/additional item cost from the profile covering most variants."""
    data = _get(f"catalog/blueprints/{blueprint_id}/print_providers/{provider_id}/shipping.json", token=token)
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


def providers(blueprint_id: int, token: str) -> list[dict]:
    return _get(f"catalog/blueprints/{blueprint_id}/print_providers.json", token=token)


def provider_countries(token: str) -> dict:
    """{provider_id: country code of its production location}"""
    return {p["id"]: (p.get("location") or {}).get("country")
            for p in _get("catalog/print_providers.json", token=token)}


def compare_providers(blueprint_id: int, token: str, provider_list=providers, countries=provider_countries,
                      shipping=us_shipping) -> list[dict]:
    """Providers for a blueprint that ship to the US, cheapest US first-item shipping first,
    then shortest handling time. Production cost is not in the catalog, so it is not compared."""
    where = countries(token)
    rows = []
    for p in provider_list(blueprint_id, token):
        try:
            ship = shipping(blueprint_id, p["id"], token=token)
        except LookupError:
            continue
        rows.append({
            "provider_id": p["id"], "title": p["title"], "country": where.get(p["id"]),
            "us_first_item": ship["first_item"], "us_additional_item": ship["additional_item"],
            "us_first_item_max": ship["first_item_max"], "handling_days_max": ship["handling_days_max"],
        })
    return sorted(rows, key=lambda r: (r["us_first_item"], r["handling_days_max"], r["country"] != "US"))


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


def costs_from_shop(shop_id: str, token: str, http=http_json, today: str | None = None) -> dict:
    """Median enabled-variant cost per blueprint from existing shop products, in the
    data/printify_costs.json format. Read-only: never creates products."""
    from datetime import date
    from statistics import median
    res = http(f"{API}/shops/{shop_id}/products.json?limit=50", headers={"Authorization": f"Bearer {token}"})
    out = {}
    for prod in res["data"]:
        costs = [v["cost"] / 100 for v in prod["variants"] if v.get("is_enabled", True) and v.get("cost")]
        if costs:
            out[str(prod["blueprint_id"])] = {
                "cost": round(median(costs), 2),
                "source": f"printify shop {shop_id} product {prod['id']} (provider {prod['print_provider_id']})",
                "date": today or date.today().isoformat(),
            }
    return out


def resolve_cost(blueprint_id: int, printful_benchmark_id: int | None, manual: dict,
                 printful_fetch=pf.get_product) -> tuple[float | None, str]:
    """Best available cost and its provenance label."""
    entry = manual.get(str(blueprint_id))
    if entry:
        return float(entry["cost"]), f"owner-entered: {entry['source']} ({entry['date']})"
    if printful_benchmark_id:
        summary = pf.variant_summary(printful_fetch(printful_benchmark_id))
        return summary["cost_median"], f"BENCHMARK: Printful live cost for same blank (#{printful_benchmark_id}), not a Printify price"
    return None, "unknown: needs a draft product in the Printify shop or an owner entry"
