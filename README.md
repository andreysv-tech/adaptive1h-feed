# Adaptive 1H — resilient public Spot candle delivery

Six assets only: **BTC, ETH, SOL, XRP, DOGE, BNB**, quoted in USDT.
Intervals: **1m, 5m, 15m, 1h, 4h, 1d**. No account keys or trading operations.
This repository changes data infrastructure only, not forecasts, trading logic or statistics.

## Independent live JSON (no GitHub dependency)

- Live service: https://adaptive1h-live-feed.andreysv735727.chatgpt.site/
- Machine-readable discovery: https://adaptive1h-live-feed.andreysv735727.chatgpt.site/manifest.json
- Asset endpoint: `/api/BTC.json` (replace BTC with ETH, SOL, XRP, DOGE or BNB).
- Direct backup verification: `/api/BTC.json?exchange=Bybit`.

The service runs on separate Sites/Cloudflare infrastructure and queries exchanges **on request**, including after 23:00. It does not download GitHub files, call GitHub Actions, or wait for a GitHub schedule. Every JSON response has `Cache-Control: no-store`. There is no static mirror pretending to be a live backup.

For each :50 forecast and the 23:30 review, a consumer must fetch current JSON: first read the GitHub snapshot if it is fresh, otherwise query the independent asset URLs. Reject any response whose `status` is not `OK` or whose `expires_at_utc` has passed. Never rely on GitHub's green badge or an old `OK` without checking capture time. This work does **not** edit any ChatGPT task or its prompt; that consumer must use these URLs to benefit from the new route. Access must be verified in the actual consuming ChatGPT environment.

No public API or scheduler guarantees 100% availability. If both exchanges fail, the live service returns HTTP 503 and diagnostics, not synthetic candles. Direct endpoints also allow data retrieval when the GitHub website or Actions is unavailable.

## Delivery verification and current ChatGPT blocker

On 2026-09-21 at 11:38 UTC an external HTTP client, outside GitHub Actions, received fresh **36/36** frames from all six live endpoints. Binance returned HTTP 403 in the live hosting region; whole-asset fallback returned valid Bybit Spot candles. Use a descriptive HTTP `User-Agent` (verification used `Adaptive1H-Verification/2.0`): the hosting edge rejected the default Python urllib client with HTTP 403 / error 1010.

**ChatGPT web-tool access is not confirmed:** the web retrieval tool reported that the site URL was not accessible, and the in-app browser returned `net::ERR_BLOCKED_BY_CLIENT` when navigating to JSON (the HTML homepage loaded). Therefore this deployment is a verified independent HTTP feed, but must not yet be described as a working automatic ChatGPT fallback. The hosting/client access restriction must be resolved, or an authorized alternative hosting domain connected and tested from the consuming ChatGPT environment. No ChatGPT automation was modified.

## Exchange selection and provenance

Binance Spot is first choice (`data-api.binance.vision`, then official Binance API hosts in the Python collector). Bybit **Spot** V5 `/v5/market/kline` is the independent provider fallback. Each collection checks an active trading pair against Binance `exchangeInfo` or Bybit `instruments-info`, including base/quote and Spot category.

**One exchange per entire asset snapshot:** all six frames must validate on the same exchange. If any frame fails, the partial result is discarded and all six frames are fetched from the next provider. No row is spliced across exchanges, and old snapshots are never merged into new arrays. The exchange may change between snapshots, which is explicitly labelled; do not concatenate them blindly.

Every asset and frame identifies `exchange`, `source_url`, capture timestamp, status, available/unavailable fields and freshness deadline. Frames also include the exact request URL and observation cutoff. Bybit candles are sorted oldest first. `taker_buy_base` and `trades` are **null** for Bybit; they are not inferred from volume. `quote_volume` comes from reported turnover. Exchange prices and volumes may differ; no attempt is made to equate Bybit prices to a Binance contract settlement source.

Rows are checked for finite OHLCV values, order, duplicates, gaps, exact interval boundaries, sufficient history and freshness. Current open candles have `complete: false`; one-second closure grace avoids claiming a just-closed bar before the observation cutoff. Requests include `endTime`/`end`, so crossing a minute during collection cannot create a future-dated row relative to the observation start.

## Files and diagnostics

- `collector.py`: standard-library Python collector, whole-asset failover, six assets in parallel.
- `live_worker.mjs`: independently deployed HTTP worker with the same validation and selection rules.
- `tests/test_collector.py` and `live_worker.test.mjs`: failure, staleness, pair, ordering, closure and no-mixing tests.
- `data/status.json`, `data/README.md`: current active feed and source diagnostics.
- `data/<ASSET>.json`: full 880-candle snapshot across six intervals; Markdown has readable excerpts.
- Optional `data/bybit-probe/`: isolated real Bybit verification output; never merged into the active feed.

Historical `data/HYPE.*` files remain unchanged and are not active inputs. Git history is preserved. Failed collections remain in Actions logs and never replace the last published snapshot. Its original expiry remains intact, so it cannot masquerade as fresh. Optional Bybit probes remain in runner temporary storage.

## Schedule and deadline handling

Both workflows use standard **UTC cron, 24/7**, without `timezone` or a local-hour cutoff:

- `Collect public market candles`: `2-57/5 * * * *` (every five minutes).
- `Watchdog rescue public market candles`: `4,39 * * * *` (hourly outcome/forecast rescue checks).
- Both support manual `workflow_dispatch` on `main`; collector also supports the isolated Bybit probe.

Israel is UTC+02 in winter and UTC+03 in summer, so :04/:39 every UTC hour are also :04/:39 every Israel hour. No duplicated DST schedules or missing operating windows are needed.

The watchdog reads `data/status.json` and all six asset files after acquiring the same concurrency group as the collector and updating its checkout. If missing, expired, inconsistent or missing the current hourly candle, it runs the existing collector directly and commits the validated result. It never dispatches another workflow. There are no push or workflow_run triggers and no trigger loops.

Both paths stage collection outside `data/`, retry up to three times, require 36/36 frames, matching status/file metadata, fresh assets and frames, whole-asset exchange consistency, full candle windows and correct hourly closure flags **before publishing**. Git history and inactive historical files are preserved. A non-forced push follows pull/rebase and another validation; conflicts fail visibly instead of overwriting a concurrent snapshot. The published remote tip is validated again after push.

**Availability limit:** both schedules still depend on the GitHub scheduler. GitHub may delay or drop either schedule; a second workflow mitigates missed runs but cannot guarantee :04/:39 or continuous freshness. The unchanged 120-second TTL is shorter than the five-minute schedule, and :39 alone cannot supply a fresh snapshot at :50. The independent live endpoint remains the existing on-demand fallback. Guaranteed deadlines require a scheduler outside GitHub (with authorized Actions dispatch credentials) or a consumer fetching live data; neither is silently configured by this repository change.

Incident 2026-10-03: run #820 was successful at 14:32 UTC, with 36/36 frames. Earlier run #819 was at 11:06 UTC, already a 3h25 gap. No subsequent run existed when investigated, despite the workflow's operating window; the workflow had not changed since September 21. Evidence locates the interruption before collector execution, in schedule delivery. GitHub does not expose the internal reason for a missing event, so a scheduler outage or timezone-specific defect cannot be proven from repository logs. Timezone syntax is supported, not inherently invalid. See [GitHub schedule limitations](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

## Validation and operation

```sh
python -m unittest discover -s tests -v
node --test live_worker.test.mjs
python collector.py
python verify_snapshot.py
python collector.py --exchange Bybit --output data/bybit-probe
```

A successful Actions run requires the current collector step to succeed, exactly six active assets, **36/36** fresh frames and one exchange per asset. No user trading credentials are needed.

The live worker is deployed separately from GitHub; updating this repository alone does not redeploy it. Publish the exact tested `live_worker.mjs` as the independent service's worker entrypoint. The source and tests are retained here for reproducibility.

API specifications: [Binance public market data](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints), [Bybit candles](https://bybit-exchange.github.io/docs/v5/market/kline), [Bybit instrument availability](https://bybit-exchange.github.io/docs/v5/market/instrument).
