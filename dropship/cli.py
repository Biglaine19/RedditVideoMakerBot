"""Command line entry points.

  python -m dropship.cli research    # refresh candidate DB (Printify catalog, Etsy fees)
  python -m dropship.cli research-printful  # alternative: Printful catalog, Shopify fees
  python -m dropship.cli providers   # compare Printify providers (needs PRINTIFY_TOKEN)
  python -m dropship.cli candidates  # print ranked candidates
  python -m dropship.cli calendar    # 14-day content plan (JSON) for review
  python -m dropship.cli report      # daily summary from connected sources
"""
import csv
import json
import sys
from datetime import date
from pathlib import Path

from .config import DATA_DIR, Settings
from .economics import ETSY
from .fulfillment.orders import OrderStore
from .marketing import content
from .reporting import dashboard
from .research import candidates as cand
from .research import printify_catalog as pfy
from .research.candidates import Spec

# Niche approved by owner 2026-10-15. Video-fit / quality / IP scores are
# analyst judgement (1-5), not measured data.
NICHE = "houseplant-lovers"
AUDIENCE = "plant parent"
# Printify blueprint ids (Printify Choice provider), with the matching Printful
# product as a cost benchmark until real Printify costs are available.
PRINTIFY_SPECS = [
    Spec(12, "T-SHIRT", 4, 2, 1, "Bella+Canvas 3001", printful_benchmark_id=71),
    Spec(706, "T-SHIRT", 4, 2, 1, "Comfort Colors 1717; earthy colours suit niche", printful_benchmark_id=586),
    Spec(6, "T-SHIRT", 4, 2, 1, "Gildan 5000; budget blank", printful_benchmark_id=None),
    Spec(49, "HOODIE", 3, 2, 1, "Gildan 18000 crewneck; seasonal", printful_benchmark_id=145),
    Spec(77, "HOODIE", 3, 2, 1, "Gildan 18500 hoodie; seasonal", printful_benchmark_id=146),
    Spec(478, "MUG", 4, 2, 1, "Ceramic mug 11/15oz; breakage risk", printful_benchmark_id=19),
    Spec(400, "STICKER", 3, 1, 1, "Kiss-cut stickers; low-ticket add-on", printful_benchmark_id=358),
]

SPECS = [
    Spec(71, "T-SHIRT", 4, 2, 1, "Bella+Canvas 3001; staple tee, wide size range"),
    Spec(586, "T-SHIRT", 4, 2, 1, "Comfort Colors 1717; heavier garment-dyed tee, earthy colours suit niche"),
    Spec(145, "HOODIE", 3, 2, 1, "Gildan 18000 crewneck; seasonal (autumn/winter)"),
    Spec(146, "HOODIE", 3, 2, 1, "Gildan 18500 hoodie; seasonal"),
    Spec(19, "MUG", 4, 2, 1, "11oz white mug; gift item, breakage risk in transit"),
    Spec(358, "STICKER", 3, 1, 1, "Kiss-cut stickers; low ticket, good add-on"),
    Spec(1, "POSTER", 4, 2, 1, "Matte poster; botanical illustration fit"),
    Spec(2, "FRAMED-POSTER", 4, 3, 1, "Framed poster; higher ticket, damage risk"),
    Spec(367, "TOTE", 4, 2, 1, "Eco tote; plant-shop / market use case"),
    Spec(206, "HAT", 3, 2, 1, "Embroidered dad hat"),
    Spec(861, "CANDLE", 3, 3, 1, "Soy candle; needs warning labels, no scent/health claims"),
]


def research(out=sys.stdout, supplier: str = "printify") -> list[dict]:
    conn = cand.connect()
    errors = []
    manual = pfy.load_manual_costs()
    specs = PRINTIFY_SPECS if supplier == "printify" else SPECS
    for spec in specs:
        try:
            row = (cand.evaluate_printify(spec, NICHE, manual) if supplier == "printify"
                   else cand.evaluate(spec, NICHE))
            cand.upsert(conn, row)
        except Exception as exc:  # noqa: BLE001 - one bad product must not stop the run
            errors.append(f"product {spec.product_id}: {exc}")
    rows = cand.ranked(conn, NICHE)
    csv_path = Path(DATA_DIR) / "candidates.csv"
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    for e in errors:
        print("ERROR", e, file=out)
    print(f"{len(rows)} candidates -> {csv_path}", file=out)
    return rows


def show_candidates(out=sys.stdout) -> None:
    rows = cand.ranked(cand.connect(), NICHE)
    print(f"{'score':>5} {'chan':7} {'cost':>6} {'ship':>5} {'price':>6} {'contrib*':>8} {'w/offsite':>9} "
          f"{'status':15} title", file=out)

    def f(v, fmt):
        return format(v, fmt) if v is not None else "-".rjust(int(fmt.split(".")[0]))

    for r in rows:
        print(f"{r['score']:5.1f} {(r['channel'] or ''):7} {f(r['cost_median'], '6.2f')} {r['shipping_cost']:5.2f} "
              f"{f(r['price'], '6.2f')} {f(r['contribution_before_ads'], '8.2f')} "
              f"{f(r['contribution_if_offsite_ad'], '9.2f')} {r['status']:15} {r['title']}", file=out)
    print("* estimates before ad spend, free shipping to buyer, demand unverified unless recorded.\n"
          "  w/offsite = contribution if the sale is attributed to Etsy Offsite Ads (15%).\n"
          "  status cost_unverified = cost is a Printful BENCHMARK, not a Printify price.", file=out)


def make_calendar(out=sys.stdout) -> None:
    top = [r["title"].split("|")[0].strip() for r in cand.ranked(cand.connect(), NICHE)[:4]]
    plan = content.calendar(top, AUDIENCE, ["#plantsoftiktok", "#houseplants", "#plantparent"], date.today())
    path = Path(DATA_DIR) / "content_calendar.json"
    path.write_text(json.dumps(plan, indent=2))
    print(f"{len(plan)} draft posts -> {path}", file=out)


def compare_providers(out=sys.stdout) -> dict:
    """US shipping / handling per provider for each candidate blueprint, plus real costs
    from existing shop products. Read-only Printify calls."""
    settings = Settings.from_env()
    if not settings.printify_token:
        print("PRINTIFY_TOKEN not set", file=out)
        return {}
    token = settings.printify_token
    result = {}
    for spec in PRINTIFY_SPECS:
        rows = pfy.compare_providers(spec.product_id, token)
        result[str(spec.product_id)] = rows
        print(f"blueprint {spec.product_id} ({spec.notes.split(';')[0]}): {len(rows)} providers ship to US", file=out)
        for r in rows[:5]:
            print(f"  {r['provider_id']:>4} {r['title'][:24]:24} {r['country'] or '?':3} "
                  f"ship ${r['us_first_item']:.2f} (+${r['us_additional_item']:.2f})  "
                  f"handling {r['handling_days_max']}d", file=out)
    path = Path(DATA_DIR) / "printify_providers.json"
    path.write_text(json.dumps({"date": date.today().isoformat(), "blueprints": result}, indent=2))
    print(f"-> {path}", file=out)
    if settings.printify_shop_id:
        costs = pfy.costs_from_shop(settings.printify_shop_id, token)
        print(f"{len(costs)} blueprints with real costs in shop {settings.printify_shop_id}", file=out)
        if costs:
            cost_path = Path(DATA_DIR) / "printify_costs.json"
            merged = dict(pfy.load_manual_costs(cost_path), **costs)
            cost_path.write_text(json.dumps(merged, indent=2))
            print(f"-> {cost_path}", file=out)
    return result


def report(out=sys.stdout) -> None:
    import sqlite3
    store = OrderStore(sqlite3.connect(Path(DATA_DIR) / "dropship.db"))
    settings = Settings.from_env()
    sources = dashboard.Sources(fees=ETSY)  # Etsy receipts / ad sources plug in here once connected
    if not settings.etsy_oauth_token:
        print("note: Etsy not connected; sales metrics UNAVAILABLE", file=out)
    print(dashboard.render_text(dashboard.build(sources, store)), file=out)


def main(argv=None) -> None:
    cmd = (argv or sys.argv[1:] or ["candidates"])[0]
    {"research": research, "research-printful": lambda: research(supplier="printful"),
     "candidates": show_candidates,
     "providers": compare_providers, "calendar": make_calendar, "report": report}[cmd]()


if __name__ == "__main__":
    main()
