# Print-on-demand business: audit and implementation plan

Status as of 2026-10-14. Channel: **Etsy**, supplier: **Printify**. All figures marked *estimate* are model outputs, not results.

## 1. Environment audit (Milestone 1)

| Area | Finding |
|---|---|
| Runtime | Linux cloud container, Python 3.13, Node 22. Ephemeral: work persists only via git push. |
| This repo | `RedditVideoMakerBot` (Reddit-to-video tool). Left untouched; new code lives in `dropship/` on branch `claude/dropshipping-system`. |
| Connected (Claude) | Gmail, Google Drive, Google Sheets, Canva, AnythingMCP, GitHub, Robinhood, TipRanks (the last two are not relevant). |
| E-commerce store | **None connected.** Target channel: Etsy. |
| Supplier | **None connected.** Target: Printify. Its public catalog gives live shipping, handling time and variants; production costs and the provider list need a token. (Printful's public catalog gives live costs; kept as benchmark/fallback.) |
| Ads / social / analytics | **None connected.** |
| Browser automation | Headless Chromium in the container only. It has no access to your logged-in accounts, and it is not used to get around logins or CAPTCHAs. |
| AnythingMCP | Has ready connectors for Etsy, Printify, Shopify, Printful. **Workspace trial ends 2026-10-15**; after that it needs a paid plan. |
| Secrets | Read from environment variables only (`config.py`) and redacted from logs (`util.redact`). Nothing secret is committed. |

## 2. Business model: Etsy + Printify (owner's choice, 2026-10-14)

**Print-on-demand with original designs, sold on Etsy, fulfilled by Printify.** Shopify + Printful remains supported in code (`research-printful`) as a fallback.

Why it fits: Etsy brings marketplace search traffic (no store to drive traffic to from zero), and Printify has a native Etsy integration that imports orders and pushes tracking back automatically.

**Etsy rules that apply (from vendor/third-party guides; check Etsy's Creativity Standards in the Help Center):**
- POD is allowed only for **your own designs**. Reselling generic items is prohibited.
- Printify must be added as a **production partner** in shop settings, and each listing's "Who made it?" must say another company or person.
- Keep design source files and creation dates; they help if a listing is flagged for originality.

**Provisional niche: houseplant lovers.** Demand still **unverified**. Etsy search volume for candidate phrases is the first evidence to collect.

## 3. Unit economics

Run `python -m dropship.cli candidates`. Snapshot: `data/candidates.csv`.

Printify token verified 2026-10-15: one shop, **"My new store" (id 29248056), sales channel `etsy`**. With the owner's approval, 7 unpublished drafts titled "Cost check - do not publish" (Rooted & Thriving art, Printify Choice) were created to read **real production costs**; none is published to Etsy or ordered.

| Input | Etsy + Printify | Source |
|---|---|---|
| Shipping (US, first item) | tee $3.99, sweatshirt/hoodie $7.39, mug $5.79, stickers $4.29 | **Live** Printify catalog API, provider "Printify Choice" (99) |
| Handling time | up to 10 days, every provider | Live Printify catalog API (same value for all providers, so not a differentiator) |
| Production cost (base size / largest size) | B+C 3001 $11.29 / $20.44 (5XL); CC 1717 $12.65 / $18.34; Gildan 5000 $9.68 / $13.47; crewneck $17.87 / $25.17; hoodie $21.58 / $25.66; mug 11oz $5.03 (15oz $6.13); stickers $1.42 (smallest) / $2.32 (largest) | **Live** Printify cost from shop drafts, `data/printify_costs.json` |
| Etsy fees | 6.5% transaction (on price + shipping), 3% + $0.25 payment, $0.20 listing renewal per sale | Third-party 2026 guides; etsy.com/legal/fees blocked automated fetch |
| Offsite Ads | 15% of an attributed order (opt-out allowed under $10k/yr sales; 12% and mandatory above) | Same |

**Provider comparison** (`python -m dropship.cli providers`, snapshot `data/printify_providers.json`). Printify Choice has the cheapest US shipping for all 7 blueprints, $0.20-0.90 less than the next provider:

| Blueprint | US providers | Best (US shipping) | Runner-up |
|---|---|---|---|
| 12 Bella+Canvas 3001 | 18 | Printify Choice $3.99 | Printful / Underground Threads / Monster Digital $4.49 |
| 706 Comfort Colors 1717 | 10 | Printify Choice $3.99 | Printful / SwiftPOD $4.49 |
| 6 Gildan 5000 | 19 | Printify Choice $3.99 | Underground Threads / Monster Digital $4.49 |
| 49 Gildan 18000 crewneck | 16 | Printify Choice $7.39 | SwiftPOD / Printful $7.99 |
| 77 Gildan 18500 hoodie | 15 | Printify Choice $7.39 | Underground Threads / SwiftPOD $7.99 |
| 478 Mug 11/15oz | 6 | Printify Choice $5.79 | SwiftPOD / Monster Digital $6.69 |
| 400 Kiss-cut stickers | 2 | Printify Choice $4.29 | SPOKE $4.49 |

The final choice must also weigh production cost, which differs by provider and is still unknown.

Estimates from `candidates` (suggested price, free shipping, **real Printify base-size costs**, before ads):

| Product | Cost | Price | Contribution | If Offsite Ads sale |
|---|---|---|---|---|
| Comfort Colors 1717 tee | $12.65 | $39.99 | $18.27 | $12.27 |
| Bella+Canvas 3001 tee | $11.29 | $36.99 | $16.98 | $11.43 |
| Gildan 18000 crewneck | $17.87 | $59.99 | $27.32 | $18.32 |
| Gildan 18500 hoodie | $21.58 | $67.99 | $30.66 | $20.46 |
| Gildan 5000 tee | $9.68 | $32.99 | $15.05 | $10.10 |
| Mug 11oz | $5.03 | $25.99 | $11.71 | $7.81 |
| Stickers (smallest) | $1.42 | $14.99 | $7.12 | $4.87 |

Plus sizes cost up to $9 more (B+C 3001 5XL $20.44), so set higher Etsy variant prices for 2XL+ rather than one price for all sizes.

The suggested prices come from the target-margin formula and sit at the high end for Etsy; they have not been checked against competitors. **Recommendation:** opt out of Offsite Ads while sales are under $10k/yr, unless tests show it brings profitable incremental orders.

**Cost drafts:** the 7 drafts stay in the Printify shop as unpublished references; delete them or turn them into real listings later. Re-read costs any time with `PRINTIFY_SHOP_ID=29248056 python -m dropship.cli providers`.

## 4. Estimated costs (nothing purchased)

| Item | Cost | When |
|---|---|---|
| Etsy shop | One-time setup fee reported at $15-29 (varies by region); $0.20 per listing, renewed every 4 months or on each sale | Shop creation |
| Printify | Free plan; pay per order. Printify Premium (paid, optional) lowers product costs; evaluate after sales start | First order |
| Samples | About $15-40 each | Before launch (photos/video) |
| AnythingMCP | Paid plan after 2026-10-15 trial (optional) | Optional: direct tokens work without it |
| Ads (Etsy Ads, Offsite Ads) | $0 until you approve | Milestone 9+ |

## 5. What is built (`dropship/`)

| Module | Function | Tested |
|---|---|---|
| `research/printify_catalog.py` | Live Printify shipping/handling/variants; cost from your Printify shop products (token), owner-entered `data/printify_costs.json` (source + date required), or labelled Printful benchmark | yes |
| `research/candidates.py` | Candidate DB, ranking, Etsy fee economics incl. "if Offsite Ads" column, demand evidence | yes |
| `economics.py` | Shopify and Etsy fee models | yes |
| `channels/etsy.py` | Etsy Open API v3: unshipped paid receipts (paginated), receipt to order mapping (unmapped SKUs go to review), idempotent tracking upload | yes (mocked API) |
| `fulfillment/printify_supplier.py` | Quote (variant cost + shipping API), **adopts the order Printify already imported from Etsy** instead of creating a duplicate, send-to-production, status/tracking mapping | yes (mocked API) |
| `fulfillment/orders.py` | State machine, idempotency, spending gates, cost-spike/stock/unmapped checks. **Detects orders the supplier sent to production without approval, alerts, and counts the spend** | yes |
| `support/`, `reporting/`, `marketing/` | As before; dashboard uses the Etsy fee model; support needs the real processing/transit windows | yes |

Tests: `python -m unittest dropship.tests.test_system` (44 tests, stdlib only). The Etsy and Printify API adapters are tested against mocks only; they have not been run against the real APIs yet.

**Spending safety:** live orders need `DROPSHIP_LIVE_ORDERS=1` and non-zero limits. **In Printify, set the Etsy store's order approval to Manual.** Printify's docs say new stores have automatic approval for their first 24 hours, and with automatic approval imported orders go to production (and are charged) on their own, bypassing these limits.

## 6. Milestones

| # | Milestone | Status | Blocked on |
|---|---|---|---|
| 1 | Environment audit | Done | none |
| 2 | Niche and product research | Niche approved 2026-10-15: houseplant lovers | Demand evidence |
| 3 | Supplier evaluation / unit economics | Done: real Printify costs, Printify Choice cheapest US shipping | none |
| 4 | Listings | Designs v1 done; trademark screen: 3 clear, Propagation Station on hold (registered mark) | Etsy shop setup, Manual order approval |
| 5 | Fulfillment, support, analytics | Built, tested on mocks | Etsy + Printify credentials |
| 6 | Marketing | Draft calendar | Designs, samples, social accounts |
| 7 | End-to-end test | Simulated only | One real sample order (your approval) |
| 8-10 | Launch, live ops, optimisation | Not started | Budget: owner set $0 on 2026-10-15 (no ads, no paid samples; all spending limits stay 0) |

Note: Etsy has no sandbox. The safe end-to-end test is a real order you pay for yourself (as a sample), or a Printify draft that is never sent to production.

## 7. Decisions and actions needed from you

1. **Approve or change the niche.**
2. **Open an Etsy shop** (identity, bank and billing verification are yours to complete; I cannot do these).
3. **Create a Printify account**, connect it to Etsy with Printify's official integration, and **set order approval to Manual**.
4. **Printify API token:** done 2026-10-15. Still to do: add environment secret `PRINTIFY_SHOP_ID` = `29248056`, and in Printify set order approval to **Manual**.
5. **Etsy API (optional at first):** register an app at https://www.etsy.com/developers/register. Add the env secrets `ETSY_API_KEY` (`keystring:shared_secret`), `ETSY_OAUTH_TOKEN` and `ETSY_SHOP_ID`. Alternatively, connect Etsy through AnythingMCP (paid after its trial ends tomorrow). Printify's integration covers orders and tracking without this; the Etsy API is for reporting and support lookups.
6. **Spending limits:** per order, per day, sample budget, ad budget ($0 default), refund authority (you approve all by default), maximum total loss, and Offsite Ads opt-out (recommended).
7. **Designs:** original artwork, or approval for Canva/AI-assisted designs. Etsy's originality rules make your own design work important.
8. **Business basics:** support email, return policy confirmation (made-to-order: replace or refund damaged/misprinted items; no change-of-mind returns), tax setup (Etsy collects US marketplace sales tax in most states; check income tax with an accountant).

## 8. Risks

- Thin margins with paid ads (see §3).
- Demand unverified; a niche may not convert.
- POD quality complaints (print fading, sizing): mitigated by ordering samples and a size chart.
- Mug breakage: covered by the reprint policy; tracked via `needs_review`.
- Etsy account risk: originality flags or slow dispatch can restrict a new shop. Mitigate with original designs, production-partner disclosure, and listing processing times that match Printify's real handling time (up to 10 days).
- Printify auto-approval bypassing spending limits (see §5).
- Platform policy: content automation must use official APIs/schedulers; no fake engagement or reviews.
- Candle: safety-warning labelling required; no therapeutic or scent-benefit claims.
