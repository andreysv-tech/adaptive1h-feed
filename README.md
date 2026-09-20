# Adaptive 1H: six-asset Binance Spot candle feed

This collects **public market candles only**, no account access and no trading API key.
It fetches BTC, ETH, SOL, XRP, DOGE and BNB from **Binance Spot**. Verify each corresponding Polymarket contract's official resolution source before matching its outcome. Collection intervals: 1m, 5m, 15m, 1h, 4h and 1d.

## Setup (requires a GitHub account, computer is easiest)

1. Unzip this archive. Create a **new GitHub repository** (public is simplest, because candles are public data and the assistant can inspect public repo pages). Never upload personal data, keys or Binance credentials. Add the unzipped contents, preserving the `.github/workflows/collect.yml` directory. Commit the files to the repository's **default branch**.
2. Open the repository's **Actions** tab, select `Collect public market candles` and select `Run workflow`. If prompted, enable workflows. The workflow checks all 36 asset/interval combinations and publishes diagnostics before reporting any failure.
3. Inspect the workflow result, then open `data/README.md` in the repository. Success means the table lists OK for recent 5m/15m/1h for **all six**. A red run, ERROR or STALE requires diagnosis; do not infer candles exist merely because a scheduled action is enabled.
4. Send ChatGPT your repository URL plus the URL of `data/README.md`. Confirm the assistant can actually read the file and its timestamps through its current tools. The automation must be updated with that exact verified URL or compatible connected source. If a public page is stale or inaccessible to tools, a compatible GitHub connector may be needed; do not assume it works without a test.
5. Once this end-to-end check passes, review 3-5 successive cycles for reliable freshness. Only then should you rely on a pre-hour forecast. This is a research feed, not an order-execution system.

## What the collector writes

- `data/README.md`: compact status matrix, one row per asset, publication time.
- `data/<ASSET>.md`: web-readable source/timestamps and last 55 candles per interval. The `Closed?` column is important at XX:50; the latest 1h is still incomplete.
- `data/<ASSET>.json`: **full fetched sequence**, complete vs incomplete explicitly marked; high-volume history for 1h EMA200 and pattern calculations.
- `data/status.json`: machine-readable health and per-source errors.

Every scheduled refresh is best effort: GitHub Actions can be delayed or dropped. No forecasts or probabilities are generated in this collector. It records facts for a separate analyst/model. The assistant must reject stale files and cannot treat a URL as proof of real-time access.

## Local test (optional)

```bash
python collector.py
python -m unittest discover -s tests -v
```

The program uses only Python's standard library. Public Binance Spot endpoint: `https://data-api.binance.vision/api/v3/klines`.

## Schedule and active data

The enabled schedule runs at :43, :48 and :53 during hours 07–22 in `Asia/Jerusalem`, including daylight-saving changes. The :43 and :48 runs prepare snapshots for forecasts at 07:50–22:50; :53 is a later refresh and cannot be used retroactively for a :50 forecast. GitHub may delay scheduled jobs, so always check snapshot age.

Only BTC, ETH, SOL, XRP, DOGE and BNB belong to the active feed (36/36 frames). Use `data/status.json` and `data/README.md` as the current asset manifest, not a directory listing. Historical `data/HYPE.*` files are retained unchanged for reference and are no longer refreshed or part of health checks. No Binance Futures requests are made.

## Time alignment

Timestamps are UTC ISO-8601. Local reporting uses `Asia/Jerusalem`. In live reporting, forecasts must be published **before** the next window opens, and only observations already available at prediction time can be used. Never label an incomplete kline as finished.
