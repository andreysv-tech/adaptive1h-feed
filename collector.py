#!/usr/bin/env python3
"""Public Spot candles. One exchange per asset snapshot; no trading operations."""
import argparse
import json
import math
import os
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ASSETS = ('BTC', 'ETH', 'SOL', 'XRP', 'DOGE', 'BNB')
INTERVAL_LIMITS = {'1m': 120, '5m': 160, '15m': 160, '1h': 240, '4h': 120, '1d': 80}
INTERVAL_MS = dict(zip(INTERVAL_LIMITS, (60000, 300000, 900000, 3600000, 14400000, 86400000)))
BYBIT_INTERVALS = dict(zip(INTERVAL_LIMITS, ('1', '5', '15', '60', '240', 'D')))
SPOT_HOSTS = ('https://data-api.binance.vision', 'https://api.binance.com', 'https://api1.binance.com')
BYBIT_HOST = 'https://api.bybit.com'
OUTPUT_DIR = Path(os.environ.get('OHLCV_OUTPUT_DIR', 'data'))
SNAPSHOT_MAX_AGE_MS = 120000
FIELDS = ['open_ms', 'open_utc', 'close_ms', 'open', 'high', 'low', 'close', 'volume', 'quote_volume', 'complete']


def now_ms():
    return int(time.time() * 1000)


def utc(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def local(ms):
    return datetime.fromtimestamp(ms / 1000, ZoneInfo('Asia/Jerusalem')).isoformat(timespec='seconds')


class FeedError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def request_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Adaptive1H-Public-Feed/2.0', 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=8) as response:
        return json.load(response)


def bybit_result(payload):
    if not isinstance(payload, dict) or payload.get('retCode') != 0:
        raise FeedError('ERROR', 'Bybit API error: ' + str(payload)[:200])
    result = payload.get('result', {})
    if result.get('category') != 'spot':
        raise FeedError('ERROR', 'Bybit response is not Spot')
    return result


def verify_pair(exchange, host, asset):
    symbol = asset + 'USDT'
    if exchange == 'Binance':
        url = host + '/api/v3/exchangeInfo?' + urllib.parse.urlencode({'symbol': symbol})
        rows = request_json(url).get('symbols', [])
        ok = any(r.get('symbol') == symbol and r.get('status') == 'TRADING' and
                 r.get('baseAsset') == asset and r.get('quoteAsset') == 'USDT' and
                 r.get('isSpotTradingAllowed') is True for r in rows)
    else:
        url = host + '/v5/market/instruments-info?' + urllib.parse.urlencode({'category': 'spot', 'symbol': symbol})
        rows = bybit_result(request_json(url)).get('list', [])
        ok = any(r.get('symbol') == symbol and r.get('status') == 'Trading' and
                 r.get('baseCoin') == asset and r.get('quoteCoin') == 'USDT' for r in rows)
    if not ok:
        raise FeedError('SYMBOL_UNAVAILABLE', exchange + ' has no active Spot ' + symbol)
    return url


def normalize(rows, exchange, interval, capture_ms):
    period = INTERVAL_MS[interval]
    if not isinstance(rows, list) or len(rows) < INTERVAL_LIMITS[interval]:
        raise FeedError('ERROR', 'Insufficient candle history')
    candles = []
    for raw in rows:
        if not isinstance(raw, list) or len(raw) < (11 if exchange == 'Binance' else 7):
            raise FeedError('ERROR', 'Malformed candle')
        opened = int(raw[0])
        closed = int(raw[6]) if exchange == 'Binance' else opened + period - 1
        o, h, l, c, v = map(float, raw[1:6])
        quote = float(raw[7] if exchange == 'Binance' else raw[6])
        taker = float(raw[9]) if exchange == 'Binance' else None
        trades = int(raw[8]) if exchange == 'Binance' else None
        nums = [o, h, l, c, v, quote] + ([] if taker is None else [taker])
        if not all(math.isfinite(n) and n >= 0 for n in nums) or l > min(o, c) or h < max(o, c) or l <= 0:
            raise FeedError('ERROR', 'Invalid OHLCV')
        if opened % period or closed != opened + period - 1 or opened > capture_ms:
            raise FeedError('ERROR', 'Invalid candle boundaries')
        if trades is not None and (trades < 0 or taker > v + 1e-8):
            raise FeedError('ERROR', 'Invalid trade fields')
        candles.append(dict(open_ms=opened, open_utc=utc(opened), close_ms=closed,
                            open=o, high=h, low=l, close=c, volume=v, quote_volume=quote,
                            taker_buy_base=taker, trades=trades, complete=closed < capture_ms - 1000))
    candles.sort(key=lambda c: c['open_ms'])
    if any(b['open_ms'] - a['open_ms'] != period for a, b in zip(candles, candles[1:])):
        raise FeedError('ERROR', 'Duplicate candle or gap')
    # Require the current bar; allow 5 seconds for a newly opened bar to appear.
    expected_open = ((capture_ms - 5000) // period) * period
    if candles[-1]['open_ms'] < expected_open:
        raise FeedError('STALE', 'Latest candle is stale')
    complete = [c for c in candles if c['complete']]
    if not complete:
        raise FeedError('ERROR', 'No closed candles')
    if complete[-1]['close_ms'] + 1 < ((capture_ms - 1000) // period) * period:
        raise FeedError('STALE', 'Last completed candle is stale')
    return candles


def fetch_frame(exchange, host, asset, interval, capture_ms=None):
    started = now_ms() if capture_ms is None else capture_ms
    params = {'symbol': asset + 'USDT', 'limit': INTERVAL_LIMITS[interval]}
    if exchange == 'Binance':
        params.update(interval=interval, endTime=started)
        path = '/api/v3/klines'
    else:
        params.update(category='spot', interval=BYBIT_INTERVALS[interval], end=started)
        path = '/v5/market/kline'
    url = host + path + '?' + urllib.parse.urlencode(params)
    raw = request_json(url)
    if exchange == 'Bybit':
        result = bybit_result(raw)
        if result.get('symbol') != asset + 'USDT':
            raise FeedError('ERROR', 'Wrong response symbol')
        raw = result.get('list')
    candles = normalize(raw, exchange, interval, started)
    captured = now_ms() if capture_ms is None else capture_ms
    complete = [c for c in candles if c['complete']]
    return dict(status='OK', exchange=exchange, market=exchange + ' Spot', source=url,
                source_url=url, symbol=asset+'USDT', interval=interval,
                observation_started_at_utc=utc(started), captured_at_utc=utc(captured),
                expires_at_utc=utc(started + SNAPSHOT_MAX_AGE_MS),
                available_fields=FIELDS + (['taker_buy_base', 'trades'] if exchange == 'Binance' else []),
                unavailable_fields=[] if exchange == 'Binance' else ['taker_buy_base', 'trades'],
                last_completed_close_utc=utc(complete[-1]['close_ms'] + 1),
                closed_candles=len(complete), candles=candles)


def collect_asset(asset, capture_ms=None, exchange=None):
    if asset not in ASSETS or exchange not in (None, 'Binance', 'Bybit'):
        raise ValueError('Unsupported asset/exchange')
    attempts = []
    routes = [('Binance', h) for h in SPOT_HOSTS] + [('Bybit', BYBIT_HOST)]
    for name, host in routes:
        if exchange and name != exchange:
            continue
        started = now_ms() if capture_ms is None else capture_ms
        try:
            pair_url = verify_pair(name, host, asset)
            # Each attempt is a whole asset. Discard ALL partial frames on failure.
            frames = {i: fetch_frame(name, host, asset, i, capture_ms) for i in INTERVAL_LIMITS}
            finished = now_ms() if capture_ms is None else capture_ms
            if finished - started >= SNAPSHOT_MAX_AGE_MS:
                raise FeedError('STALE', 'Collection exceeded snapshot freshness limit')
            return dict(asset=asset, status='OK', exchange=name, market=name+' Spot',
                        source_url=host, pair_source_url=pair_url, capture_time_utc=utc(finished),
                        collection_started_at_utc=utc(started), expires_at_utc=utc(started+SNAPSHOT_MAX_AGE_MS),
                        available_fields=frames['1m']['available_fields'],
                        unavailable_fields=frames['1m']['unavailable_fields'], attempts=attempts, frames=frames)
        except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
            attempts.append(dict(exchange=name, source_url=host, status=getattr(exc, 'status', 'ERROR'),
                                 error=f'{type(exc).__name__}: {str(exc)[:240]}'))
    captured = now_ms() if capture_ms is None else capture_ms
    status = 'STALE' if any(a['status'] == 'STALE' for a in attempts) else 'ERROR'
    return dict(asset=asset, status=status, exchange=None, market='Spot', source_url=None,
                capture_time_utc=utc(captured), expires_at_utc=utc(captured), available_fields=[],
                unavailable_fields=FIELDS+['taker_buy_base', 'trades'], attempts=attempts,
                frames={i: dict(status=status, exchange=None, market='Spot', source_url=None, source=None,
                                captured_at_utc=utc(captured), available_fields=[], candles=[], errors=attempts)
                        for i in INTERVAL_LIMITS})


def fmt(n):
    return 'null' if n is None else f'{n:.10g}'


def make_asset_markdown(payload):
    lines = [f'# {payload["asset"]}USDT — public candles', '',
             f'Status: {payload["status"]}; exchange: {payload["exchange"]}',
             f'Captured: {payload["capture_time_utc"]}; expires: {payload["expires_at_utc"]}',
             f'Source: {payload["source_url"]}',
             'Available fields: ' + ', '.join(payload['available_fields']),
             'Unavailable fields: ' + ', '.join(payload['unavailable_fields']), '',
             'Public data only. No forecasts. Exchange prices may differ.', '']
    for interval, f in payload['frames'].items():
        lines += [f'## {interval} — {f["status"]}', f'Source: {f.get("source_url")}',
                  f'Captured: {f["captured_at_utc"]}',
                  f'Last completed close: {f.get("last_completed_close_utc")}', '',
                  '| Open UTC | O | H | L | C | Volume | Taker buy | Closed? |',
                  '|---|---:|---:|---:|---:|---:|---:|---|']
        for c in f['candles'][-55:]:
            values = ' | '.join(fmt(c[k]) for k in ('open','high','low','close','volume','taker_buy_base'))
            lines.append(f'| {c["open_utc"]} | {values} | {"yes" if c["complete"] else "no"} |')
        lines.append('')
    lines += ['Attempts: ' + json.dumps(payload['attempts'])]
    return '\n'.join(lines)+'\n'


def collect(capture_ms=None, output=None, exchange=None):
    output = Path(output) if output is not None else OUTPUT_DIR
    output.mkdir(parents=True, exist_ok=True)
    started = now_ms() if capture_ms is None else capture_ms
    with ThreadPoolExecutor(max_workers=6) as pool:
        payloads = list(pool.map(lambda a: collect_asset(a, capture_ms, exchange), ASSETS))
    summary = dict(schema_version=2, captured_at_utc=utc(started), captured_at_israel=local(started),
                   generated_at_utc=utc(now_ms() if capture_ms is None else capture_ms),
                   snapshot_max_age_seconds=SNAPSHOT_MAX_AGE_MS//1000,
                   purpose='public candle data, not trading signals', assets={}, total_frames=36)
    successful = 0
    lines = ['# Adaptive 1H data status', '', f'Collection started: {utc(started)} / {local(started)}',
             'Freshness must be checked at read time; OK is a collection-time result.', '',
             '| Asset | Exchange | 1m | 5m | 15m | 1h | 4h | 1d |', '|---|---|---|---|---|---|---|---|']
    for p in payloads:
        a = p['asset']
        (output/f'{a}.json').write_text(json.dumps(p, separators=(',', ':'), allow_nan=False)+'\n')
        (output/f'{a}.md').write_text(make_asset_markdown(p))
        meta = {k:v for k,v in p.items() if k != 'frames'}
        meta['frames'] = {i: {k:v for k,v in f.items() if k != 'candles'} for i,f in p['frames'].items()}
        summary['assets'][a] = meta
        successful += sum(f['status']=='OK' for f in p['frames'].values())
        lines.append('| '+a+' | '+str(p['exchange'])+' | '+' | '.join(f'[{f["status"]}]({a}.md)' for f in p['frames'].values())+' |')
    summary['successful_frames'] = successful
    lines += ['', f'Available fresh frames: **{successful}/36**.', 'ERROR/STALE is a data-access problem, not a No Trade signal.']
    (output/'status.json').write_text(json.dumps(summary, indent=2, allow_nan=False)+'\n')
    (output/'README.md').write_text('\n'.join(lines)+'\n')
    print(f'Collection: {utc(started)}; fresh frames={successful}/36', flush=True)
    return successful


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--exchange', choices=['Binance', 'Bybit'])
    parser.add_argument('--output', default=str(OUTPUT_DIR))
    args = parser.parse_args()
    result = collect(output=args.output, exchange=args.exchange)
    # Diagnostics are still written and committed by the workflow on failure.
    raise SystemExit(0 if result == 36 else 1)
