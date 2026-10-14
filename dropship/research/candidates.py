"""Candidate product database and ranking.

Scores combine:
  * live supplier data (cost, stock)           -> source "printful_api"
  * published shipping rates                   -> source "published"
  * analyst judgement (video fit, quality/IP)   -> source "judgement", 1-5
  * demand evidence                            -> "unverified" until real data
    (search-trend export, marketplace data, test-campaign results) is recorded.

Nothing here invents demand or sales numbers: unverified demand is scored 0
and shown as such.
"""
import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from ..config import DATA_DIR
from ..economics import ETSY, contribution, suggested_price
from . import printful_catalog as pf
from . import printify_catalog as pfy

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    key TEXT PRIMARY KEY,
    niche TEXT NOT NULL,
    supplier TEXT NOT NULL,
    supplier_product_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    shipping_group TEXT NOT NULL,
    cost_median REAL,
    variants_in_stock INTEGER,
    shipping_cost REAL,
    shipping_source TEXT,
    price REAL,
    contribution_before_ads REAL,
    margin_before_ads_pct REAL,
    video_fit INTEGER,
    quality_risk INTEGER,
    ip_risk INTEGER,
    demand_evidence TEXT,
    demand_score REAL,
    score REAL,
    status TEXT NOT NULL DEFAULT 'candidate',
    notes TEXT,
    updated_at TEXT NOT NULL
);
"""
# Columns added after the first release; connect() adds them to older DBs.
EXTRA_COLUMNS = {
    "channel": "TEXT",
    "cost_source": "TEXT",
    "handling_days_max": "INTEGER",
    "contribution_if_offsite_ad": "REAL",
}


@dataclass(frozen=True)
class Spec:
    product_id: int
    shipping_group: str
    video_fit: int  # 1-5 judgement: how demonstrable/visual in short video
    quality_risk: int  # 1-5 judgement: 5 = high risk of complaints
    ip_risk: int  # 1-5: 1 = original designs only, low risk
    notes: str = ""
    printful_benchmark_id: int | None = None  # same blank at Printful, for cost benchmarking


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or Path(DATA_DIR) / "dropship.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return init(sqlite3.connect(path))


def init(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    have = {r[1] for r in conn.execute("PRAGMA table_info(candidates)")}
    for col, typ in EXTRA_COLUMNS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE candidates ADD COLUMN {col} {typ}")
    return conn


def score(margin_before_ads_pct: float, contribution_usd: float, stock_ok: bool,
          video_fit: int, quality_risk: int, ip_risk: int, demand_score: float) -> float:
    """0-100. Weighted; demand counts 0 until evidence is recorded."""
    if not stock_ok:
        return 0.0
    s = 0.0
    s += 25 * min(max(margin_before_ads_pct, 0) / 60, 1)  # margin headroom
    s += 20 * min(max(contribution_usd, 0) / 20, 1)  # absolute $ per order (ad budget room)
    s += 15 * (video_fit - 1) / 4
    s += 10 * (5 - quality_risk) / 4
    s += 10 * (5 - ip_risk) / 4
    s += 20 * demand_score  # 0..1, only from recorded evidence
    return round(s, 1)


def evaluate(spec: Spec, niche: str, fetch=pf.get_product) -> dict:
    product = fetch(spec.product_id)
    summary = pf.variant_summary(product)
    ship_first, _ = pf.PUBLISHED_US_SHIPPING[spec.shipping_group]
    cost = summary["cost_median"]
    price = suggested_price(cost, ship_first)
    econ = contribution(price, cost, ship_first)
    margin = round(100 * econ.breakeven_cpa / price, 1)
    stock_ok = summary["variants_in_stock"] > 0
    return {
        "key": f"printful:{spec.product_id}:{niche}",
        "niche": niche,
        "supplier": "printful",
        "supplier_product_id": spec.product_id,
        "title": product["product"]["title"],
        "shipping_group": spec.shipping_group,
        "cost_median": cost,
        "variants_in_stock": summary["variants_in_stock"],
        "shipping_cost": ship_first,
        "shipping_source": pf.SHIPPING_SOURCE,
        "price": price,
        "contribution_before_ads": econ.breakeven_cpa,
        "margin_before_ads_pct": margin,
        "video_fit": spec.video_fit,
        "quality_risk": spec.quality_risk,
        "ip_risk": spec.ip_risk,
        "demand_evidence": "unverified",
        "demand_score": 0.0,
        "score": score(margin, econ.breakeven_cpa, stock_ok, spec.video_fit,
                       spec.quality_risk, spec.ip_risk, 0.0),
        "status": "candidate" if stock_ok else "paused_out_of_stock",
        "notes": spec.notes,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "channel": "shopify",
        "cost_source": "printful catalog API (live)",
        "handling_days_max": pf.PUBLISHED_FULFILLMENT_DAYS[1],
        "contribution_if_offsite_ad": None,
    }


def evaluate_printify(spec: Spec, niche: str, manual_costs: dict, shipping=pfy.us_shipping,
                      variant_list=pfy.variants, resolve=pfy.resolve_cost, bp=pfy.blueprint) -> dict:
    """Printify blueprint (Printify Choice provider) sold on Etsy, free shipping to buyer."""
    ship = shipping(spec.product_id)
    in_stock = len(variant_list(spec.product_id))
    cost, cost_source = resolve(spec.product_id, spec.printful_benchmark_id, manual_costs)
    info = bp(spec.product_id)
    row = {
        "key": f"printify:{spec.product_id}:{niche}",
        "niche": niche,
        "supplier": "printify",
        "supplier_product_id": spec.product_id,
        "title": f"{info['title']} | {info['brand']} {info['model']}".strip(),
        "shipping_group": spec.shipping_group,
        "cost_median": cost,
        "variants_in_stock": in_stock,
        "shipping_cost": ship["first_item"],
        "shipping_source": ship["source"],
        "demand_evidence": "unverified",
        "demand_score": 0.0,
        "video_fit": spec.video_fit,
        "quality_risk": spec.quality_risk,
        "ip_risk": spec.ip_risk,
        "notes": spec.notes,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "channel": "etsy",
        "cost_source": cost_source,
        "handling_days_max": ship["handling_days_max"],
    }
    if cost is None:
        return dict(row, price=None, contribution_before_ads=None, margin_before_ads_pct=None,
                    contribution_if_offsite_ad=None, score=0.0, status="needs_cost")
    price = suggested_price(cost, ship["first_item"], fees=ETSY)
    econ = contribution(price, cost, ship["first_item"], fees=ETSY)
    offsite = contribution(price, cost, ship["first_item"],
                           fees=replace(ETSY, offsite_ads_share=1.0))
    margin = round(100 * econ.breakeven_cpa / price, 1)
    stock_ok = in_stock > 0
    return dict(
        row, price=price, contribution_before_ads=econ.breakeven_cpa, margin_before_ads_pct=margin,
        contribution_if_offsite_ad=offsite.breakeven_cpa,
        score=score(margin, econ.breakeven_cpa, stock_ok, spec.video_fit, spec.quality_risk, spec.ip_risk, 0.0),
        status=("candidate" if stock_ok else "paused_out_of_stock")
        if cost_source.startswith(("owner", "printify")) else "cost_unverified",
    )


def upsert(conn: sqlite3.Connection, row: dict) -> None:
    # Preserve recorded demand evidence across catalog refreshes.
    existing = conn.execute(
        "SELECT demand_evidence, demand_score FROM candidates WHERE key=?", (row["key"],)
    ).fetchone()
    if row["demand_evidence"] == "unverified" and existing and existing["demand_evidence"] != "unverified":
        row = dict(row, demand_evidence=existing["demand_evidence"], demand_score=existing["demand_score"])
    if row["contribution_before_ads"] is not None and row["demand_evidence"] != "unverified":
        row["score"] = score(row["margin_before_ads_pct"], row["contribution_before_ads"],
                             row["variants_in_stock"] > 0, row["video_fit"], row["quality_risk"],
                             row["ip_risk"], row["demand_score"])
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    conn.execute(f"INSERT OR REPLACE INTO candidates ({cols}) VALUES ({marks})", tuple(row.values()))
    conn.commit()


def record_demand(conn: sqlite3.Connection, key: str, evidence: dict, demand_score: float) -> None:
    """Attach real demand evidence (source, date, metric) and rescore."""
    if not 0 <= demand_score <= 1:
        raise ValueError("demand_score must be within 0..1")
    for field in ("source", "date", "metric"):
        if field not in evidence:
            raise ValueError(f"evidence needs '{field}'")
    row = dict(conn.execute("SELECT * FROM candidates WHERE key=?", (key,)).fetchone())
    if row["contribution_before_ads"] is None:
        raise ValueError(f"{key} has no verified cost yet")
    row["demand_evidence"] = json.dumps(evidence)
    row["demand_score"] = demand_score
    row["score"] = score(row["margin_before_ads_pct"], row["contribution_before_ads"],
                         row["variants_in_stock"] > 0, row["video_fit"], row["quality_risk"],
                         row["ip_risk"], demand_score)
    upsert(conn, row)


def ranked(conn: sqlite3.Connection, niche: str | None = None) -> list[dict]:
    q = "SELECT * FROM candidates" + (" WHERE niche=?" if niche else "") + " ORDER BY score DESC"
    return [dict(r) for r in conn.execute(q, (niche,) if niche else ())]
