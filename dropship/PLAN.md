# Print-on-demand business: audit and implementation plan

Status as of 2026-10-14. All figures marked *estimate* are model outputs, not results.

## 1. Environment audit (Milestone 1)

| Area | Finding |
|---|---|
| Runtime | Linux cloud container, Python 3.13, Node 22. Ephemeral: work persists only via git push. |
| This repo | `RedditVideoMakerBot` (Reddit-to-video tool). Left untouched; new code lives in `dropship/` on branch `claude/dropshipping-system`. |
| Connected (Claude) | Gmail, Google Drive, Google Sheets, Canva, AnythingMCP, GitHub, Robinhood, TipRanks (the last two are not relevant). |
| E-commerce store | **None connected.** No Shopify/Etsy/WooCommerce credentials. |
| Supplier | **None connected.** Printful public catalog (live product costs, stock) works without a token; shipping-rate and order APIs need a token. |
| Ads / social / analytics | **None connected.** |
| Browser automation | Headless Chromium in the container only. It has no access to your logged-in accounts, and it is not used to get around logins or CAPTCHAs. |
| AnythingMCP | Has ready connectors for Shopify, Printful, Printify, Gelato. **Workspace trial ends 2026-10-15**; after that it needs a paid plan. |
| Secrets | Read from environment variables only (`config.py`) and redacted from logs (`util.redact`). Nothing secret is committed. |

## 2. Business model recommendation

**Print-on-demand (POD) via Printful + Shopify**, rather than generic AliExpress-style dropshipping:
- Original designs: no counterfeit/IP risk, defensible brand.
- Supplier with documented API, published fulfillment (2-5 business days) and US transit (3-4 business days) times, tracking on every order.
- No inventory; nothing is produced until a customer pays.

**Provisional niche: houseplant lovers ("plant parents").** Picked for: evergreen interest, giftability, very visual for short video, and a natural fit for original illustration. **Demand is NOT yet verified**. Every candidate shows `demand=unverified` and scores 0 on demand until real evidence is recorded (see §5). Alternatives worth comparing: home coffee/espresso, trail running, birding, bookish/reading.

## 3. Unit economics (live Printful costs, published shipping)

Run `python -m dropship.cli candidates`. Snapshot in `data/candidates.csv`.

At the 45%-margin *suggested* prices the numbers look healthy, but those prices are above typical POD market prices. At typical prices (my assumption, not measured):

| Product | Price | Contribution before ads (*estimate*) |
|---|---|---|
| Bella+Canvas 3001 tee | $27.99 | $9.16 (33%) |
| Comfort Colors 1717 tee | $32.99 | $10.16 (31%) |
| 11oz mug | $22.99 | $6.48 (28%) |
| Gildan 18500 hoodie | $49.99 | $15.25 (31%) |
| Poster | $29.99 | $11.12 (37%) |

Assumptions: free shipping to customer, Shopify Payments 2.9% + $0.30, 5% refund rate.

**Implication:** the break-even ad cost per order is only about $6-15. Paid ads are likely unprofitable until creative is proven. **Strategy: organic short-form video first, bundles to lift order value, paid ads only as small, capped tests after organic winners exist.**

## 4. Estimated costs (nothing purchased)

| Item | Cost | Needed when |
|---|---|---|
| Shopify Basic | $1/mo for 3 months after a 3-day trial, then $39/mo (or $29/mo yearly) | Store build |
| Printful | Free account; pay per order at production cost | First order |
| Sample units (strongly recommended for photos/video) | About $15-40 each incl. shipping | Before launch |
| Custom domain | About $10-20/yr | Launch |
| AnythingMCP (optional bridge for Claude to Shopify/Printful) | Paid plan after 2026-10-15 trial | Optional: direct API tokens work without it |
| Ads | $0 until you approve a budget | Milestone 9+ |

## 5. What is built (`dropship/`)

| Module | Function | Tested |
|---|---|---|
| `research/` | Live Printful catalog client (cached), candidate DB (SQLite), scoring, demand-evidence recording (requires source/date/metric) | yes |
| `economics.py` | Contribution / break-even CPA / price suggestion | yes |
| `fulfillment/` | Order state machine, idempotent ingest, one draft per order (external_id), compare-and-set transitions, spending gates, cost-spike and stock checks, tracking sync, Printful adapter (drafts with `confirm=false`), offline simulator | yes (simulated end-to-end) |
| `support/` | Grounded replies (orders DB + policy only), email-matched order lookup, escalation for disputes/legal/safety/complaints, all refunds routed to a human | yes |
| `reporting/` | Daily/weekly summary; unconnected sources show `UNAVAILABLE`, never 0 | yes |
| `marketing/` | Hooks/scripts/captions by format and platform, banned-claim filter, 14-day calendar (`data/content_calendar.json`), format ranking from real analytics only | yes |
| `util.py` | JSON logs with secret redaction, HTTP retries with exponential backoff (429/5xx only) | yes |

Run tests: `python -m unittest dropship.tests.test_system` (29 tests, stdlib only, no installs).

**Spending safety:** live supplier orders need `DROPSHIP_LIVE_ORDERS=1` **and** non-zero limits (`DROPSHIP_MAX_SUPPLIER_ORDER_USD`, `DROPSHIP_MAX_DAILY_SUPPLIER_SPEND_USD`). Defaults are zero, so every order stays an unpaid Printful draft.

## 6. Milestones

| # | Milestone | Status | Blocked on |
|---|---|---|---|
| 1 | Environment audit | Done | none |
| 2 | Niche and product research | Engine done; provisional niche | Your niche approval + demand evidence |
| 3 | Supplier evaluation and unit economics | Printful costs live; shipping from published rates | `PRINTFUL_TOKEN` for API-verified shipping |
| 4 | Store design and listings | Not started | Shopify store + Admin API access |
| 5 | Fulfillment, support, analytics | Built and simulated | Shopify + Printful credentials to run against real data |
| 6 | Marketing workflow | Draft calendar generated | Designs, sample footage, social accounts |
| 7 | End-to-end test | Simulated only | Shopify test-mode checkout (Bogus Gateway) |
| 8 | Launch review | Not started | All of the above + your approval |
| 9-10 | Live operation and optimisation | Not started | Spending limits approved |

## 7. Decisions and actions needed from you

1. **Approve or change the niche** (houseplant lovers / coffee / running / birding / other).
2. **Create a Shopify account** (trial needs no card) and either:
   - create a custom app with Admin API scopes `read/write_products, read_orders, write_fulfillments, read_inventory`, and set the token as `SHOPIFY_ADMIN_TOKEN` in the environment's secrets, **or**
   - connect Shopify through AnythingMCP (trial ends tomorrow, so this requires a paid plan).
3. **Create a Printful account**, connect it to Shopify via Printful's official app, and generate a private API token (`PRINTFUL_TOKEN`). Add it as an environment secret; never paste it in chat.
4. **Set spending limits** when you are ready: per-order and daily supplier caps, sample-order budget, ad budget (default $0), refund authority (default: you approve every refund), maximum acceptable total loss.
5. **Designs:** supply original artwork, or approve AI/Canva-generated designs. Note that AI-generated art may have limited copyright protection. Avoid trademarked plant brands and copyrighted characters.
6. **Business basics:** support email/domain, business address for policies, and your sales tax registration approach (Shopify Tax can calculate; obligations depend on your state, so check with an accountant).

## 8. Risks

- Thin margins with paid ads (see §3).
- Demand unverified; a niche may not convert.
- POD quality complaints (print fading, sizing): mitigated by ordering samples and a size chart.
- Mug/framed-poster breakage: covered by the reprint policy; tracked via `needs_review`.
- Platform policy: content automation must use official APIs/schedulers; no fake engagement or reviews.
- Candle: safety-warning labelling required; no therapeutic or scent-benefit claims.
