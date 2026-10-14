"""Command line entry points.

  python -m dropship.cli research    # refresh candidate DB from live Printful catalog
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
from .fulfillment.orders import OrderStore
from .marketing import content
from .reporting import dashboard
from .research import candidates as cand
from .research.candidates import Spec

# PROVISIONAL niche pending owner approval. Video-fit / quality / IP scores are
# analyst judgement (1-5), not measured data.
NICHE = "houseplant-lovers"
AUDIENCE = "plant parent"
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


def research(out=sys.stdout) -> list[dict]:
    conn = cand.connect()
    errors = []
    for spec in SPECS:
        try:
            cand.upsert(conn, cand.evaluate(spec, NICHE))
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
    print(f"{'score':>5}  {'cost':>6} {'ship':>5} {'price':>6} {'contrib*':>8} {'margin*':>7}  demand      title", file=out)
    for r in rows:
        print(f"{r['score']:5.1f}  {r['cost_median']:6.2f} {r['shipping_cost']:5.2f} {r['price']:6.2f} "
              f"{r['contribution_before_ads']:8.2f} {r['margin_before_ads_pct']:6.1f}%  "
              f"{r['demand_evidence'][:10]:10}  {r['title']}", file=out)
    print("* estimates before ad spend, free shipping to customer; contribution = max affordable CPA", file=out)


def make_calendar(out=sys.stdout) -> None:
    top = [r["title"].split("|")[0].strip() for r in cand.ranked(cand.connect(), NICHE)[:4]]
    plan = content.calendar(top, AUDIENCE, ["#plantsoftiktok", "#houseplants", "#plantparent"], date.today())
    path = Path(DATA_DIR) / "content_calendar.json"
    path.write_text(json.dumps(plan, indent=2))
    print(f"{len(plan)} draft posts -> {path}", file=out)


def report(out=sys.stdout) -> None:
    import sqlite3
    store = OrderStore(sqlite3.connect(Path(DATA_DIR) / "dropship.db"))
    settings = Settings.from_env()
    sources = dashboard.Sources()  # Shopify / ad sources plug in here once connected
    if not settings.shopify_token:
        print("note: Shopify not connected; sales metrics UNAVAILABLE", file=out)
    print(dashboard.render_text(dashboard.build(sources, store)), file=out)


def main(argv=None) -> None:
    cmd = (argv or sys.argv[1:] or ["candidates"])[0]
    {"research": research, "candidates": show_candidates, "calendar": make_calendar, "report": report}[cmd]()


if __name__ == "__main__":
    main()
